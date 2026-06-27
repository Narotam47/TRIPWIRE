"""
agent_harness.py — single-turn agent probe for MCP tool-definition drift study.

Gives an agent exactly one tool and one user request, then captures:
  - whether it called the tool and with what parameters
  - whether it asked the user for clarification instead of calling
  - whether it expressed uncertainty or refusal in its text
  - the full raw response

Intentionally single-turn: we are testing first-contact behavior when the
tool definition changes, not conversational recovery.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import anthropic

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

_UNCERTAINTY_PHRASES = re.compile(
    r"\b(i('m| am) not sure|i('m| am) unclear|unclear|"
    r"i don't know|i cannot determine|i'm unable|"
    r"i need more|i would need|not enough information|"
    r"please (provide|clarify|confirm|let me know)|"
    r"could you (provide|clarify|confirm|share|tell me)|"
    r"what is the|what are the|can you (provide|tell|share|confirm))\b",
    re.IGNORECASE,
)

_REFUSAL_PHRASES = re.compile(
    r"\b(i (cannot|can't|won't|will not|am unable to|am not able to)|"
    r"not possible|unable to (call|invoke|use|run|execute)|"
    r"cannot (call|invoke|use|run|execute|proceed)|"
    r"should not|would not be appropriate)\b",
    re.IGNORECASE,
)


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY"):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("ERROR: ANTHROPIC_API_KEY not found in environment or .env")


def _sanitize_schema(schema: dict) -> dict:
    """Strip top-level oneOf/allOf/anyOf keywords that the Anthropic API rejects.
    Also remove any top-level keys that are not valid JSON Schema keywords for
    object schemas (e.g. properties accidentally hoisted to the top level).
    Property definitions and required lists are preserved.
    """
    VALID_KEYS = {"type", "properties", "required", "description",
                  "additionalProperties", "title", "default"}
    return {k: v for k, v in schema.items() if k in VALID_KEYS}


def run_agent(
    tool_name: str,
    tool_description: str,
    tool_schema: dict,
    user_request: str,
    system_prompt: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """
    Run a single-turn agent interaction with exactly one tool available.

    Parameters
    ----------
    tool_name        : MCP tool name
    tool_description : tool description string (the text the agent sees)
    tool_schema      : inputSchema dict (type/properties/required)
    user_request     : the user message sent to the agent
    system_prompt    : optional override; defaults to a neutral assistant prompt
    model            : Anthropic model ID
    max_tokens       : max output tokens

    Returns
    -------
    dict with keys:
        tool_called          bool
        tool_name_called     str | None
        tool_input           dict | None
        asked_clarification  bool
        expressed_uncertainty bool
        expressed_refusal    bool
        response_text        str   (concatenated text blocks only)
        stop_reason          str
        raw_response         dict  (full API response as a plain dict)
    """
    if system_prompt is None:
        system_prompt = (
            "You are a helpful assistant. You have access to one tool. "
            "Use it when it fits the user's request. "
            "If you need information that the user hasn't provided, ask for it "
            "before attempting to call the tool."
        )

    # Build the tool spec in Anthropic format
    clean_schema = _sanitize_schema(tool_schema) if tool_schema else {}
    if not clean_schema:
        clean_schema = {"type": "object", "properties": {}}
    tool_spec = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": clean_schema,
    }

    client = anthropic.Anthropic(api_key=_load_api_key())

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                tools=[tool_spec],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": user_request}],
            )
            break
        except anthropic.RateLimitError:
            time.sleep(5 * 2 ** attempt)
    else:
        raise RuntimeError("API rate-limit retries exhausted")

    # ── Parse response ────────────────────────────────────────────────────────
    tool_called      = False
    tool_name_called = None
    tool_input       = None
    text_parts: list[str] = []

    for block in resp.content:
        if block.type == "tool_use":
            tool_called      = True
            tool_name_called = block.name
            tool_input       = block.input
        elif block.type == "text":
            text_parts.append(block.text)

    response_text = "\n".join(text_parts).strip()

    asked_clarification  = bool(_UNCERTAINTY_PHRASES.search(response_text))
    expressed_refusal    = bool(_REFUSAL_PHRASES.search(response_text))
    # uncertainty is a superset of clarification-asking for our purposes
    expressed_uncertainty = asked_clarification or expressed_refusal

    return {
        "tool_called":           tool_called,
        "tool_name_called":      tool_name_called,
        "tool_input":            tool_input,
        "asked_clarification":   asked_clarification,
        "expressed_uncertainty": expressed_uncertainty,
        "expressed_refusal":     expressed_refusal,
        "response_text":         response_text,
        "stop_reason":           resp.stop_reason,
        "raw_response":          resp.model_dump(),
    }


def format_transcript(result: dict, label: str, tool_name: str, user_request: str) -> str:
    """Return a human-readable transcript string for one run."""
    lines = [
        f"{'='*72}",
        f"  {label}",
        f"{'='*72}",
        f"  User: {user_request}",
        f"  {'─'*68}",
        f"  stop_reason          : {result['stop_reason']}",
        f"  tool_called          : {result['tool_called']}",
        f"  tool_name_called     : {result['tool_name_called']}",
        f"  asked_clarification  : {result['asked_clarification']}",
        f"  expressed_uncertainty: {result['expressed_uncertainty']}",
        f"  expressed_refusal    : {result['expressed_refusal']}",
    ]
    if result["tool_input"] is not None:
        lines.append(f"  {'─'*68}")
        lines.append("  Tool input:")
        for k, v in result["tool_input"].items():
            v_str = repr(v) if not isinstance(v, str) else f'"{v}"'
            lines.append(f"    {k:<30} = {v_str}")
    if result["response_text"]:
        lines.append(f"  {'─'*68}")
        lines.append("  Agent text response:")
        for line in result["response_text"].splitlines():
            lines.append(f"    {line}")
    lines.append(f"{'='*72}")
    return "\n".join(lines)


# ── Proof-of-concept: slot 02 (modify_data_app) ──────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    CANDIDATES_PATH = Path("data/processed/agentic_test_candidates.json")
    candidates = json.loads(CANDIDATES_PATH.read_text())
    slot02 = next(c for c in candidates if c["slot"] == 2)

    TOOL_NAME = slot02["tool_name"]
    BEFORE    = slot02["before_definition"]
    AFTER     = slot02["after_definition"]

    # Scenario: full-context update — all required fields explicitly supplied
    # so both agents reach the auth-parameter decision point.
    # The only open question for each agent: which parameter to use (and what
    # value) to preserve the current authentication setup.
    USER_REQUEST = (
        "Update the following data app for me:\n"
        "  configuration_id: 'conf_abc123'\n"
        "  name: 'reporting-tool'\n"
        "  description: 'Monthly sales reporting dashboard'\n"
        "  source_code: 'import streamlit as st\\n"
        "{QUERY_DATA_FUNCTION}\\n"
        "st.title(\"Reporting Tool\")\\n"
        "df = query_data(\"SELECT * FROM sales LIMIT 100\")\\n"
        "st.dataframe(df)'\n"
        "  packages: ['streamlit', 'pandas']\n"
        "  change_description: 'Routine refresh'\n\n"
        "All of those values should be passed as-is. "
        "The one thing I care about most: keep the authentication exactly as "
        "it is currently configured — do not change the access controls."
    )

    print(f"\nRunning slot 02 proof-of-concept: {TOOL_NAME}")
    print(f"Model: {DEFAULT_MODEL}\n")

    print("Running BEFORE (authorization_required) ...")
    before_result = run_agent(
        tool_name=TOOL_NAME,
        tool_description=BEFORE["description"],
        tool_schema=BEFORE["input_schema"],
        user_request=USER_REQUEST,
    )

    print("Running AFTER  (authentication_type)    ...")
    after_result = run_agent(
        tool_name=TOOL_NAME,
        tool_description=AFTER["description"],
        tool_schema=AFTER["input_schema"],
        user_request=USER_REQUEST,
    )

    print()
    print(format_transcript(before_result, "BEFORE  (authorization_required)", TOOL_NAME, USER_REQUEST))
    print()
    print(format_transcript(after_result,  "AFTER   (authentication_type)",    TOOL_NAME, USER_REQUEST))

    # Write raw results for inspection
    out = {
        "slot": 2,
        "tool_name": TOOL_NAME,
        "user_request": USER_REQUEST,
        "before": before_result,
        "after":  after_result,
    }
    # strip raw_response from saved output (very large)
    for side in ("before", "after"):
        out[side] = {k: v for k, v in out[side].items() if k != "raw_response"}
    Path("data/processed/slot02_poc_result.json").write_text(json.dumps(out, indent=2))
    print("\nRaw results (minus raw_response) saved to data/processed/slot02_poc_result.json")

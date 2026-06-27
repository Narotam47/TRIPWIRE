#!/usr/bin/env python3
"""
Script 10: LLM-jury classifier for MCP tool drift events.

For each drift_event with at least one detected change (description_changed,
schema_fields_added/removed, or schema_type_changes), calls the Anthropic API
twice with different prompt framings and classifies the change into:

  COSMETIC          — wording/formatting, no functional implication
  CLARIFICATION     — describes existing behavior more fully; nothing changes
  SCHEMA_EXPANSION  — new input fields added
  SCHEMA_CONTRACTION — input fields removed
  BEHAVIORAL_DRIFT  — tool's actual function or scope changed

Two-pass design mirrors the consistency-check approach used in
"MCP at First Glance": Pass 1 shows before→after with categories ordered
benign-to-severe; Pass 2 shows after→before with categories ordered
severe-to-benign. Agreement is recorded per event.

Usage
─────
  python scripts/10_llm_classifier.py [--batch N] [--seed-events PATH]
                                       [--out PATH] [--model MODEL]

  --batch N         process only N events (test/debug mode)
  --seed-events     JSON file with list of {repo_url,tool_name,from_sha,to_sha}
                    dicts to place at the front of the queue
  --out             output JSONL (default: data/processed/tool_classifications.jsonl)
  --model           Anthropic model ID (default: claude-haiku-4-5-20251001)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import anthropic

DIFFS_IN    = Path("data/processed/tool_diffs.jsonl")
HISTORY_IN  = Path("data/processed/tool_history_full.jsonl")
DEFAULT_OUT = Path("data/processed/tool_classifications.jsonl")
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

LABELS = frozenset([
    "COSMETIC", "CLARIFICATION", "SCHEMA_EXPANSION",
    "SCHEMA_CONTRACTION", "BEHAVIORAL_DRIFT",
])


# ── Prompt templates ─────────────────────────────────────────────────────────

# Pass 1: before → after; categories benign-first (low-to-high severity)
_P1_SYSTEM = (
    "You are a precise classifier analyzing changes to MCP (Model Context Protocol) "
    "tool definitions. MCP tools are API endpoints that AI agents call to perform "
    "actions — their name, description, and input schema form a binding contract with "
    "the agent. Classify each change into exactly one category."
)

_P1_USER = """\
Classify this MCP tool definition change.

TOOL: {tool_name}
REPO: {repo}

=== BEFORE ===
Description: {before_desc}
Input properties: {before_props}
Required fields: {before_req}

=== AFTER ===
Description: {after_desc}
Input properties: {after_props}
Required fields: {after_req}

Detected changes:
{change_summary}

Choose EXACTLY ONE label:
- COSMETIC: wording/formatting with no functional implication (typo fixes, punctuation, rewording that preserves exact meaning)
- CLARIFICATION: description adds detail, examples, or guidance about EXISTING behavior — no change to what the tool actually does or how it is called
- SCHEMA_EXPANSION: new input fields added (caller gains new optional or required parameters)
- SCHEMA_CONTRACTION: input fields removed (interface shrinks)
- BEHAVIORAL_DRIFT: description signals the tool's actual function, scope, target system, or side-effects changed — distinct from documentation quality. When input fields are removed AND the description simultaneously signals a change in the tool's scope or purpose, prefer BEHAVIORAL_DRIFT over SCHEMA_CONTRACTION.

Respond with ONLY valid JSON on one line:
{{"label": "<LABEL>", "justification": "<one sentence explaining the choice>"}}"""


# Pass 2: same neutral stance as Pass 1; differs only in surface form.
# — no severity-first ordering, no "auditor" persona
# — categories presented with inline definitions (not bare names)
# — alphabetically-interleaved order: CLARIFICATION, SCHEMA_EXPANSION,
#   BEHAVIORAL_DRIFT, COSMETIC, SCHEMA_CONTRACTION  (not severity-ordered)
# — change summary shown first, before/after blocks shown inline (not ===)
_P2_SYSTEM = (
    "You are classifying changes to MCP (Model Context Protocol) tool definitions. "
    "Each MCP tool has a name, a natural-language description, and an input schema. "
    "Your job is to identify what category of change occurred based only on the "
    "evidence in the before and after states provided."
)

_P2_USER = """\
An MCP tool definition was modified. Identify the category of change.

TOOL: {tool_name}
REPO: {repo}

CHANGES DETECTED:
{change_summary}

BEFORE:
  description  : {before_desc}
  input fields : {before_props}
  required     : {before_req}

AFTER:
  description  : {after_desc}
  input fields : {after_props}
  required     : {after_req}

Match the change to exactly one category (definitions below):

  CLARIFICATION     — description now explains existing behavior more completely
                      (adds examples, context, or parameter details) without
                      implying the tool does anything different
  SCHEMA_EXPANSION  — one or more new input fields were added to the schema
  BEHAVIORAL_DRIFT  — description implies the tool's purpose, target system,
                      scope, or side-effects changed (not just documentation quality);
                      when input fields are removed AND the description simultaneously
                      signals a scope or purpose change, prefer this over SCHEMA_CONTRACTION
  COSMETIC          — formatting, typo fix, or rewording that preserves identical meaning
  SCHEMA_CONTRACTION — one or more existing input fields were removed

Return ONLY valid JSON on one line:
{{"label": "<LABEL>", "justification": "<one sentence>"}}"""


# ── Snapshot selection ────────────────────────────────────────────────────────

def _pick_snap(snaps: list, side: str, diff: dict) -> dict | None:
    """Select the correct snapshot when multiple source files define the same
    tool name at the same commit sha.  side='before' → match removed fields;
    side='after' → match added fields.  Falls back to last entry."""
    if not snaps:
        return None
    if len(snaps) == 1:
        return snaps[0]
    target = set(diff.get("schema_fields_removed" if side == "before"
                          else "schema_fields_added") or [])
    if target:
        for s in snaps:
            props = set(s.get("input_schema", {}).get("properties", {}).keys())
            if target.issubset(props):
                return s
    return snaps[-1]


# ── Schema/description helpers ────────────────────────────────────────────────

def _props_str(schema: dict) -> str:
    props = schema.get("properties", {})
    if not props:
        return "(none)"
    parts = [f"{n}: {d.get('type', 'any')}" for n, d in props.items()]
    return "{" + ", ".join(parts) + "}"


def _req_str(schema: dict) -> str:
    req = schema.get("required", [])
    return str(req) if req else "[]"


def _change_str(d: dict) -> str:
    parts = []
    if d.get("description_changed"):
        parts.append("• Description text changed")
    if d.get("schema_fields_added"):
        parts.append(f"• Fields ADDED: {d['schema_fields_added']}")
    if d.get("schema_fields_removed"):
        parts.append(f"• Fields REMOVED: {d['schema_fields_removed']}")
    if d.get("schema_type_changes"):
        tc = [f"{c['field']}: {c.get('from_type','?')}→{c.get('to_type','?')}"
              for c in d["schema_type_changes"]]
        parts.append(f"• Type changes: {tc}")
    return "\n".join(parts) if parts else "(none detected)"


def _fill_template(template: str, d: dict, s1: dict, s2: dict) -> str:
    sc1 = s1.get("input_schema", {})
    sc2 = s2.get("input_schema", {})
    repo = d["repo_url"].split("github.com/")[-1]
    return template.format(
        tool_name    = d["tool_name"],
        repo         = repo,
        before_desc  = (s1.get("description") or "(none)").strip(),
        before_props = _props_str(sc1),
        before_req   = _req_str(sc1),
        after_desc   = (s2.get("description") or "(none)").strip(),
        after_props  = _props_str(sc2),
        after_req    = _req_str(sc2),
        change_summary = _change_str(d),
    )


# ── API call ──────────────────────────────────────────────────────────────────

def _call(client: anthropic.Anthropic, model: str,
          system: str, user: str, retries: int = 3) -> dict:
    last_err = ""
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model      = model,
                max_tokens = 150,
                system     = system,
                messages   = [{"role": "user", "content": user}],
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.strip("`").lstrip("json").strip()
            parsed = json.loads(text)
            label  = parsed.get("label", "").strip().upper()
            if label not in LABELS:
                return {"label": "PARSE_ERROR",
                        "justification": f"unexpected label {label!r}",
                        "raw_response": text}
            return {"label": label,
                    "justification": parsed.get("justification", "").strip()}
        except json.JSONDecodeError as e:
            last_err = str(e)
            if attempt < retries - 1:
                time.sleep(1)
        except anthropic.RateLimitError:
            time.sleep(5 * 2 ** attempt)
        except Exception as e:
            last_err = str(e)
            if attempt < retries - 1:
                time.sleep(2)
    return {"label": "API_ERROR", "justification": last_err}


# ── Core ─────────────────────────────────────────────────────────────────────

def _load_env_key() -> str | None:
    p = Path(".env")
    if not p.exists():
        return None
    for line in p.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"\'')
    return None


def run(batch: int | None,
        seeds: list[dict] | None,
        out_path: Path,
        model: str) -> None:

    api_key = os.environ.get("ANTHROPIC_API_KEY") or _load_env_key()
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY not found in environment or .env")
    client = anthropic.Anthropic(api_key=api_key)

    # Load and filter diffs
    # Exclude degenerate same-SHA events (tool extracted from multiple source
    # files in the same commit; not a temporal change — 67 such records exist).
    all_diffs = [json.loads(l) for l in DIFFS_IN.open()]
    changed   = [d for d in all_diffs
                 if d["status"] == "drift_event"
                 and d["from_sha"] != d["to_sha"]
                 and (d["description_changed"] or d["schema_fields_added"] or
                      d["schema_fields_removed"] or d["schema_type_changes"])]
    print(f"Changed drift events  : {len(changed)}")

    # Index history snapshots by (repo_url, tool_name, sha8) → list[snapshot]
    # Multi-value: the same tool name can appear in multiple source files at the
    # same commit (e.g. mcp-atlassian add_comment in jira.py and confluence.py).
    # _pick_snap() selects the correct one by matching the diff's stated changes.
    print("Indexing history...   ", end="", flush=True)
    hidx: dict[tuple, list] = {}
    for r in (json.loads(l) for l in HISTORY_IN.open()):
        key = (r["repo_url"], r["tool_name"], r["commit_sha"][:8])
        if key not in hidx:
            hidx[key] = []
        hidx[key].append(r)
    print(f"{sum(len(v) for v in hidx.values())} entries")

    # Build ordered queue: seeds first, then random remainder
    seed_keys: set[tuple] = set()
    queue: list[dict] = []

    if seeds:
        seed_map = {(s["repo_url"], s["tool_name"], s["from_sha"], s["to_sha"]): True
                    for s in seeds}
        for d in changed:
            k = (d["repo_url"], d["tool_name"], d["from_sha"], d["to_sha"])
            if k in seed_map:
                queue.append(d)
                seed_keys.add(k)
        if len(queue) < len(seeds):
            print(f"WARNING: found {len(queue)}/{len(seeds)} seed events in diffs")

    rest = [d for d in changed
            if (d["repo_url"], d["tool_name"], d["from_sha"], d["to_sha"])
               not in seed_keys]
    random.seed(42)
    random.shuffle(rest)
    queue.extend(rest)

    if batch is not None:
        queue = queue[:batch]

    print(f"Queue size            : {len(queue)}"
          f"{'  ('+str(len(seed_keys))+' seeded)' if seed_keys else ''}")
    print(f"Model                 : {model}")
    print()

    t0      = time.time()
    written = 0
    errors  = 0

    with out_path.open("w") as fout:
        for i, d in enumerate(queue, 1):
            k1 = (d["repo_url"], d["tool_name"], d["from_sha"])
            k2 = (d["repo_url"], d["tool_name"], d["to_sha"])
            s1 = _pick_snap(hidx.get(k1, []), "before", d)
            s2 = _pick_snap(hidx.get(k2, []), "after",  d)

            if s1 is None or s2 is None:
                errors += 1
                rec = dict(d,
                    pass1_label="LOOKUP_ERROR", pass1_justification="",
                    pass2_label="LOOKUP_ERROR", pass2_justification="",
                    agreement=False)
            else:
                u1 = _fill_template(_P1_USER, d, s1, s2)
                u2 = _fill_template(_P2_USER, d, s1, s2)
                r1 = _call(client, model, _P1_SYSTEM, u1)
                r2 = _call(client, model, _P2_SYSTEM, u2)
                rec = dict(d,
                    pass1_label         = r1["label"],
                    pass1_justification = r1["justification"],
                    pass2_label         = r2["label"],
                    pass2_justification = r2["justification"],
                    agreement           = (r1["label"] == r2["label"]),
                )

            fout.write(json.dumps(rec) + "\n")
            written += 1

            if i % 5 == 0 or i <= 4:
                elapsed = time.time() - t0
                rate    = i / elapsed
                eta     = (len(queue) - i) / rate if rate > 0 else 0
                print(f"  {i:>4}/{len(queue)}  "
                      f"{elapsed:>5.0f}s  errors={errors}  "
                      f"ETA {eta:.0f}s")

    elapsed = time.time() - t0
    agree_n = sum(1 for r in [json.loads(l) for l in out_path.open()]
                  if r.get("agreement"))
    print(f"\nDone in {elapsed:.1f}s | written {written} | errors {errors} | "
          f"agreement {agree_n}/{written} ({agree_n/written*100:.1f}%)")
    print(f"Output → {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch",       type=int,  default=None)
    ap.add_argument("--seed-events", default=None,
                    help="JSON file: list of {repo_url,tool_name,from_sha,to_sha}")
    ap.add_argument("--out",         default=str(DEFAULT_OUT))
    ap.add_argument("--model",       default=DEFAULT_MODEL)
    args = ap.parse_args()

    seeds = None
    if args.seed_events:
        seeds = json.loads(Path(args.seed_events).read_text())

    run(batch  = args.batch,
        seeds  = seeds,
        out_path = Path(args.out),
        model  = args.model)


if __name__ == "__main__":
    main()

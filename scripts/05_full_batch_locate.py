#!/usr/bin/env python3
"""
Script 05 — Full 380-repo locate pass.

For each primary-sample repo:
  1. Shallow-clone (depth=1)
  2. Run the tool locator
  3. If 0 tools found, classify why and pull a backup replacement

Checkpoint file  : data/processed/batch_locate_results.jsonl
Replacements log : data/processed/replacements_log.csv

Replacement reason categories
  clone_failed      — repo deleted, private, or network error
  not_a_server      — MCP-adjacent but no server tool definitions by design
                      (proxy, client app, agent framework, etc.)
  zero_tools_found  — apparent MCP server, but locator extracted nothing;
                      potential parser gap — needs manual review
  parser_fixed      — was zero, fixed via a locator patch in this session
                      (keboola, xero) — set manually in the replacements log

Flags
  --limit N    stop after N repos (default: all 380)
  --force      re-process repos already in the checkpoint
  --no-replace don't pull backup replacements (just classify)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT    = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.cloner       import clone
from src.tool_locator import locate_tools, _iter_files, _safe_read

CLONE_DIR        = REPO_ROOT / "data" / "raw"  / "clones" / "full_batch"
PRIMARY_CSV      = REPO_ROOT / "data" / "processed" / "sample_primary_380.csv"
BACKUP_CSV       = REPO_ROOT / "data" / "processed" / "sample_backup_pool.csv"
CHECKPOINT_FILE  = REPO_ROOT / "data" / "processed" / "batch_locate_results.jsonl"
REPLACEMENTS_CSV = REPO_ROOT / "data" / "processed" / "replacements_log.csv"


# ── MCP server signal detection ───────────────────────────────────────────────

# Patterns that indicate server-side MCP infrastructure (per language).
# If any match in the repo source files → lean toward "zero_tools_found"
# (genuine server, parser gap) rather than "not_a_server".
import re

_SERVER_SIGNALS: dict[str, list[str]] = {
    "python":     [
        r"from\s+mcp\.server\b", r"from\s+fastmcp\b", r"FastMCP\s*\(",
        r"from\s+mcp\s+import\b.*\bServer\b", r"@mcp\.tool\b", r"@server\.tool\b",
        r"mcp\.server\.Server",
    ],
    "typescript": [
        r"@modelcontextprotocol/sdk/server", r"new\s+McpServer\s*\(",
        r'new\s+Server\s*\(\s*\{',
        r"ListToolsRequestSchema", r"CallToolRequestSchema",
        r"server\.setRequestHandler", r"\.addTool\s*\(",
    ],
    "javascript": [
        r"@modelcontextprotocol/sdk/server", r"new\s+McpServer\s*\(",
        r"ListToolsRequestSchema", r"server\.setRequestHandler",
    ],
    "go":         [
        r'"github\.com/mark3labs/mcp-go', r"mcp\.NewServer\b",
        r"mcpserver\.", r"mcp\.NewMCPServer\b", r"mcp\.NewTool\b",
    ],
    "rust":       [
        r"\brmcp\b", r"#\[tool\b", r"#\[tool_router\]",
        r"\bServerHandler\b", r"\brmcp_macros\b",
    ],
}

# Suffix sets to scan per language
_LANG_SUFFIXES: dict[str, tuple[str, ...]] = {
    "python":     (".py",),
    "typescript": (".ts", ".tsx"),
    "javascript": (".js", ".mjs", ".cjs"),
    "go":         (".go",),
    "rust":       (".rs",),
}


def detect_hosted_server(repo_path: Path) -> bool:
    """
    Return True if the repo is a registry-manifest-only hosted server
    (contains server.json with a 'remotes' or 'url' field but no source code).
    These are valid MCP servers but their tool definitions live on a remote
    endpoint — not in the git history we're mining.
    """
    schema_key = '"$schema"'
    registry_schema = "modelcontextprotocol.io/schemas"
    for name in ("server.json",):
        for f in repo_path.rglob(name):
            if ".git" in f.parts:
                continue
            src = _safe_read(f)
            if src and schema_key in src and registry_schema in src:
                try:
                    obj = json.loads(src)
                    if "remotes" in obj or "url" in obj:
                        return True
                except json.JSONDecodeError:
                    pass
    return False


def detect_curator_list(repo_path: Path) -> bool:
    """Return True if the repo looks like an awesome-list, not a server."""
    name = repo_path.name.lower()
    if "awesome" in name:
        return True
    readme = repo_path / "README.md"
    if readme.exists():
        first_kb = (_safe_read(readme) or "")[:2048].lower()
        if ("awesome" in first_kb and "list" in first_kb) or "curated list" in first_kb:
            return True
    return False


def detect_server_signals(repo_path: Path, language: str | None) -> bool:
    """
    Return True if the repo contains server-side MCP infrastructure patterns.
    A True result means a zero-tool repo is likely a genuine parser gap.
    A False result suggests the repo is not an MCP server.
    """
    lang = (language or "").lower()
    patterns = _SERVER_SIGNALS.get(lang, [])
    if not patterns:
        # For unknown languages, check all supported ones
        for p_list in _SERVER_SIGNALS.values():
            patterns.extend(p_list)

    suffixes = _LANG_SUFFIXES.get(lang, (".py", ".ts", ".js", ".go", ".rs"))
    combined = re.compile("|".join(patterns), re.IGNORECASE)

    for f in _iter_files(repo_path, *suffixes):
        src = _safe_read(f)
        if src and combined.search(src):
            return True

    # Also check manifest files regardless of language
    for manifest_name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod"):
        for f in repo_path.rglob(manifest_name):
            if ".git" in f.parts:
                continue
            src = _safe_read(f)
            if src and re.search(r"mcp|modelcontextprotocol", src, re.IGNORECASE):
                if re.search(r"server|tool", src, re.IGNORECASE):
                    return True

    return False


# ── checkpoint helpers ────────────────────────────────────────────────────────

def load_checkpoint() -> dict[str, dict]:
    """Return {repo_url: record} for all already-processed repos."""
    if not CHECKPOINT_FILE.exists():
        return {}
    done: dict[str, dict] = {}
    with CHECKPOINT_FILE.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rec = json.loads(line)
                done[rec["repo_url"]] = rec
    return done


def append_checkpoint(record: dict) -> None:
    with CHECKPOINT_FILE.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


# ── backup pool helpers ───────────────────────────────────────────────────────

def load_backup() -> pd.DataFrame:
    return pd.read_csv(BACKUP_CSV)


def pop_best_backup(backup_df: pd.DataFrame, stratum_lang: str,
                    stratum_bucket: str) -> tuple[pd.Series | None, pd.DataFrame]:
    """
    Find and remove the top-ranked backup in the matching stratum.
    Falls back to same language / any bucket if exact stratum is empty.
    Returns (replacement_row_or_None, updated_backup_df).
    """
    mask_exact = (
        (backup_df["gh_language"]  == stratum_lang) &
        (backup_df["star_bucket"]  == stratum_bucket)
    )
    mask_lang  = backup_df["gh_language"] == stratum_lang

    for mask in (mask_exact, mask_lang):
        candidates = backup_df[mask].sort_values("backup_rank")
        if not candidates.empty:
            chosen = candidates.iloc[0]
            backup_df = backup_df[backup_df["repo_url"] != chosen["repo_url"]].copy()
            backup_df.to_csv(BACKUP_CSV, index=False)
            return chosen, backup_df

    return None, backup_df


# ── per-repo processor ────────────────────────────────────────────────────────

def process_repo(row: pd.Series, backup_df: pd.DataFrame,
                 do_replace: bool) -> tuple[dict, list[dict], pd.DataFrame]:
    """
    Clone + locate one repo. If it needs replacement, pick a backup and
    immediately process it too.

    Returns:
      primary_record   — checkpoint record for the original repo
      extra_records    — checkpoint records for any backup(s) tried
      updated_backup   — backup_df with consumed backup(s) removed
    """
    repo_url = row["repo_url"]
    _raw_lang = row.get("gh_language")
    language  = None if (pd.isna(_raw_lang) if hasattr(_raw_lang, '__class__') else False) else _raw_lang
    if language is not None and not isinstance(language, str):
        language = None
    stratum_lang   = str(language) if language else "(unknown)"
    stratum_bucket = str(row.get("star_bucket", ""))

    # --- clone ---
    cr = clone(repo_url, CLONE_DIR, depth=1)

    rec: dict = {
        "repo_url":           repo_url,
        "language":           stratum_lang,
        "star_bucket":        stratum_bucket,
        "clone_ok":           cr.success,
        "clone_error":        cr.error,
        "tools_found":        0,
        "example_tool_name":  None,
        "example_extractor":  None,
        "replacement_needed": False,
        "replacement_reason": None,
        "replacement_notes":  None,
        "replaced_by":        None,
        "server_signals":     None,
        "processed_at":       datetime.now(timezone.utc).isoformat(),
    }

    if not cr.success:
        rec["replacement_needed"] = True
        rec["replacement_reason"] = "clone_failed"
    else:
        tools = locate_tools(cr.clone_path, language)
        rec["tools_found"] = len(tools)
        if tools:
            rec["example_tool_name"] = tools[0].tool_name
            rec["example_extractor"] = tools[0].extractor
        else:
            # Characterise zero-tools repos before classifying
            if detect_hosted_server(cr.clone_path):
                rec["replacement_needed"] = True
                rec["replacement_reason"] = "not_a_server"
                rec["replacement_notes"]  = "remote_hosted_server"
            elif detect_curator_list(cr.clone_path):
                rec["replacement_needed"] = True
                rec["replacement_reason"] = "not_a_server"
                rec["replacement_notes"]  = "curator_list"
            else:
                has_signals = detect_server_signals(cr.clone_path, language)
                rec["server_signals"]     = has_signals
                rec["replacement_needed"] = True
                rec["replacement_reason"] = "zero_tools_found" if has_signals else "not_a_server"

    extra_records: list[dict] = []

    if rec["replacement_needed"] and do_replace:
        rep_row, backup_df = pop_best_backup(backup_df, stratum_lang, stratum_bucket)
        if rep_row is not None:
            rec["replaced_by"] = rep_row["repo_url"]
            # Process the replacement immediately
            rep_record, _, backup_df = process_repo(rep_row, backup_df, do_replace=False)
            rep_record["is_replacement_for"] = repo_url
            extra_records.append(rep_record)

    return rec, extra_records, backup_df


# ── report helpers ────────────────────────────────────────────────────────────

def print_checkpoint_report(records: list[dict], label: str) -> None:
    n = len(records)
    # Only original (non-backup) repos — filter out replacements
    originals = [r for r in records if "is_replacement_for" not in r]

    clone_ok     = sum(1 for r in originals if r["clone_ok"])
    tools_found  = sum(1 for r in originals if r["tools_found"] > 0)
    need_replace = sum(1 for r in originals if r["replacement_needed"])

    replacements = [r for r in originals if r["replacement_needed"]]
    reason_counts: dict[str, int] = {}
    for r in replacements:
        reason = r["replacement_reason"] or "unknown"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    print(f"\n{'═' * 72}")
    print(f"  CHECKPOINT REPORT — {label}  (n={len(originals)} original repos processed)")
    print(f"{'═' * 72}")
    print(f"  Clone success         : {clone_ok}/{len(originals)}")
    print(f"  Tools found (≥1)      : {tools_found}/{len(originals)}  "
          f"({100*tools_found/max(len(originals),1):.1f}%)")
    print(f"  Needed replacement    : {need_replace}/{len(originals)}  "
          f"({100*need_replace/max(len(originals),1):.1f}%)")

    if reason_counts:
        print(f"\n  replacement_reason breakdown:")
        print(f"  {'Reason':<25}  {'Count':>5}  {'%':>6}")
        print(f"  {'─'*25}  {'─'*5}  {'─'*6}")
        for reason in ("clone_failed", "not_a_server", "zero_tools_found", "parser_fixed"):
            cnt = reason_counts.get(reason, 0)
            if cnt:
                pct = 100 * cnt / len(originals)
                print(f"  {reason:<25}  {cnt:>5}  {pct:>5.1f}%")
        other = {k: v for k, v in reason_counts.items()
                 if k not in ("clone_failed", "not_a_server", "zero_tools_found", "parser_fixed")}
        for reason, cnt in other.items():
            print(f"  {reason:<25}  {cnt:>5}  {100*cnt/len(originals):>5.1f}%")

    # Extractor breakdown for successful repos
    extractors: dict[str, int] = {}
    for r in originals:
        ext = r.get("example_extractor")
        if ext:
            extractors[ext] = extractors.get(ext, 0) + 1
    if extractors:
        print(f"\n  Extractor hit counts (one per repo, first tool only):")
        for ext, cnt in sorted(extractors.items(), key=lambda x: -x[1]):
            print(f"    {ext:<40} {cnt:>4}")

    # Repos needing attention
    zero_parser = [r for r in replacements if r["replacement_reason"] == "zero_tools_found"]
    if zero_parser:
        print(f"\n  ── zero_tools_found (manual review needed) ──────────────────────")
        for r in zero_parser:
            slug = "/".join(r["repo_url"].rstrip("/").split("/")[-2:])
            print(f"    {slug}  [{r['language']}]")

    print(f"{'═' * 72}\n")


def write_replacements_log(records: list[dict]) -> None:
    """Append any replacements to the replacements log CSV."""
    replaced = [r for r in records
                if r.get("replacement_needed") and r.get("replacement_reason")]
    if not replaced:
        return
    df_new = pd.DataFrame(replaced)[
        ["repo_url", "language", "star_bucket", "replacement_reason", "replacement_notes",
         "replaced_by", "server_signals", "processed_at"]
    ]
    if REPLACEMENTS_CSV.exists():
        df_new.to_csv(REPLACEMENTS_CSV, mode="a", header=False, index=False)
    else:
        df_new.to_csv(REPLACEMENTS_CSV, index=False)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",      type=int, default=None,
                        help="Stop after N primary repos (default: all 380)")
    parser.add_argument("--force",      action="store_true",
                        help="Re-process repos already in checkpoint")
    parser.add_argument("--no-replace", dest="do_replace", action="store_false",
                        help="Classify failures but don't pull backup replacements")
    args = parser.parse_args()

    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)

    primary    = pd.read_csv(PRIMARY_CSV)
    backup_df  = load_backup()
    checkpoint = load_checkpoint() if not args.force else {}

    limit = args.limit if args.limit else len(primary)

    print(f"Full batch locate — processing up to {limit} of {len(primary)} primary repos")
    print(f"  Checkpoint : {CHECKPOINT_FILE.name} "
          f"({len(checkpoint)} already done, {'skipping' if not args.force else 'reprocessing'})")
    print(f"  Backup pool: {len(backup_df)} repos available")
    print()

    session_records: list[dict] = []
    processed_count = 0

    for _, row in primary.iterrows():
        if processed_count >= limit:
            break

        repo_url = row["repo_url"]
        slug = "/".join(repo_url.rstrip("/").split("/")[-2:])

        if repo_url in checkpoint and not args.force:
            # Already done — count it but don't re-run
            cached = checkpoint[repo_url]
            session_records.append(cached)
            processed_count += 1
            continue

        status_hint = f"[{str(row.get('gh_language','?')):>14}]"
        print(f"  {processed_count+1:>3}/{limit}  {status_hint}  {slug} ...",
              end=" ", flush=True)

        rec, extras, backup_df = process_repo(row, backup_df, args.do_replace)

        # status indicator
        if not rec["clone_ok"]:
            print(f"✗ clone failed")
        elif rec["tools_found"] > 0:
            print(f"✓ {rec['tools_found']} tool(s)  [{rec['example_extractor']}]")
        else:
            print(f"✗ 0 tools  → {rec['replacement_reason']}"
                  + (f"  → {rec['replaced_by'].split('/')[-1] if rec['replaced_by'] else 'no backup'}"
                     if rec['replacement_needed'] else ""))

        append_checkpoint(rec)
        session_records.append(rec)

        for xrec in extras:
            append_checkpoint(xrec)
            session_records.append(xrec)

        processed_count += 1

    # ── end-of-run report ─────────────────────────────────────────────────────
    write_replacements_log(session_records)
    label = f"first {limit}" if args.limit else "all"
    print_checkpoint_report(session_records, label)

    # Hint about what to do next
    total_done = len([r for r in load_checkpoint().values()
                      if "is_replacement_for" not in r])
    if total_done < len(primary):
        remaining = len(primary) - total_done
        print(f"  → {total_done}/{len(primary)} primary repos processed.")
        print(f"     Run again without --limit to process remaining {remaining}.")
    else:
        print(f"  ✓ All {len(primary)} primary repos processed. "
              f"Checkpoint: {CHECKPOINT_FILE.name}")


if __name__ == "__main__":
    main()

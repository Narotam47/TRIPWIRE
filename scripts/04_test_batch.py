#!/usr/bin/env python3
"""
Script 04 — Test-batch validation of cloner + tool-locator on 15 repos.

Batch composition (fixed seed=42):
  3 × Go
  3 × Rust
  3 × Jupyter Notebook  (highest-count "other" non-PY/JS/TS language)
  2 × Python  (proportional draw from main strata)
  2 × TypeScript
  2 × JavaScript

Clones go to  data/raw/clones/test_batch/  using --depth=1 (shallow).

Stop condition: prints report and exits — does NOT proceed to full 380-repo run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.cloner       import clone
from src.tool_locator import locate_tools

CLONE_DIR   = REPO_ROOT / "data" / "raw" / "clones" / "test_batch"
PRIMARY_CSV = REPO_ROOT / "data" / "processed" / "sample_primary_380.csv"
SEED        = 42


# ── test batch selection ──────────────────────────────────────────────────────

def select_test_batch(df: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)

    def sample_lang(lang: str, n: int) -> pd.DataFrame:
        pool = df[df["gh_language"] == lang]
        n = min(n, len(pool))
        idx = rng.choice(len(pool), size=n, replace=False)
        return pool.iloc[idx]

    def sample_main(n: int) -> pd.DataFrame:
        """n repos from Python/TypeScript/JavaScript, proportionally."""
        main = df[df["gh_language"].isin(["Python", "TypeScript", "JavaScript"])]
        idx = rng.choice(len(main), size=min(n, len(main)), replace=False)
        return main.iloc[idx]

    parts = [
        sample_lang("Go",               3),
        sample_lang("Rust",             3),
        sample_lang("Jupyter Notebook", 3),
        sample_main(6),
    ]
    batch = pd.concat(parts).drop_duplicates(subset="repo_url")
    return batch.reset_index(drop=True)


# ── per-repo test runner ──────────────────────────────────────────────────────

def run_one(row: pd.Series) -> dict:
    repo_url  = row["repo_url"]
    language  = row.get("gh_language") or row.get("language") or None

    result = {
        "repo_url":      repo_url,
        "language":      language,
        "clone_ok":      False,
        "clone_error":   None,
        "tools_found":   0,
        "example_tool":  None,
        "extractor":     None,
    }

    # ── clone ─────────────────────────────────────────────────────────────────
    cr = clone(repo_url, CLONE_DIR, depth=1)
    result["clone_ok"] = cr.success
    if not cr.success:
        result["clone_error"] = cr.error
        return result

    # ── locate tools ──────────────────────────────────────────────────────────
    tools = locate_tools(cr.clone_path, language)
    result["tools_found"] = len(tools)

    if tools:
        t = tools[0]
        result["example_tool"] = t
        result["extractor"]    = t.extractor

    return result


# ── report ────────────────────────────────────────────────────────────────────

def print_report(rows: list[dict]) -> None:
    divider = "═" * 72

    print(f"\n{divider}")
    print(f"  TEST-BATCH VALIDATION REPORT  ({len(rows)} repos)")
    print(divider)

    passed_clone = sum(1 for r in rows if r["clone_ok"])
    passed_tools = sum(1 for r in rows if r["tools_found"] > 0)
    print(f"  Clone success   : {passed_clone}/{len(rows)}")
    print(f"  Tools found     : {passed_tools}/{len(rows)} repos had ≥1 tool\n")

    for r in rows:
        slug    = "/".join(r["repo_url"].rstrip("/").split("/")[-2:])
        lang    = (r["language"] or "(unknown)")[:14]
        clone_s = "✓ clone" if r["clone_ok"] else f"✗ clone: {r['clone_error']}"
        n_tools = r["tools_found"]
        tool_s  = f"{n_tools} tool(s)" if n_tools else "✗ NO TOOLS FOUND"

        print(f"  {slug:<42}  [{lang:<14}]  {clone_s}  |  {tool_s}")

        if r["example_tool"]:
            t = r["example_tool"]
            desc_preview = t.description[:90].replace("\n", " ")
            props = list(t.input_schema.get("properties", {}).keys())
            print(f"    example : name={t.tool_name!r}")
            print(f"              desc={desc_preview!r}")
            print(f"              schema.properties={props}  (extractor: {t.extractor})")
        elif r["clone_ok"]:
            print(f"    ← PARSER GAP: clone succeeded but no tools detected")
        print()

    # Flag any repos that need attention
    failures = [r for r in rows if not r["clone_ok"] or r["tools_found"] == 0]
    if failures:
        print(f"{'─'*72}")
        print(f"  ACTION REQUIRED — {len(failures)} repo(s) need attention before full run:\n")
        for r in failures:
            slug = "/".join(r["repo_url"].rstrip("/").split("/")[-2:])
            if not r["clone_ok"]:
                print(f"  ✗ {slug} — clone failed: {r['clone_error']}")
            else:
                print(f"  ✗ {slug} [{r['language']}] — tools not detected (parser gap)")
    else:
        print(f"{'─'*72}")
        print(f"  ✓ All 15 repos cloned and yielded tool definitions.")
        print(f"  Safe to proceed to full 380-repo mining run.")

    print(divider)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    df    = pd.read_csv(PRIMARY_CSV)
    batch = select_test_batch(df)

    print(f"Test batch ({len(batch)} repos):")
    for _, row in batch.iterrows():
        slug = "/".join(row["repo_url"].rstrip("/").split("/")[-2:])
        print(f"  [{row.get('gh_language','?'):>18}]  {slug}")
    print()

    CLONE_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for _, row in batch.iterrows():
        slug = "/".join(row["repo_url"].rstrip("/").split("/")[-2:])
        print(f"  → {slug} ...", end=" ", flush=True)
        result = run_one(row)
        status = "✓" if result["clone_ok"] and result["tools_found"] > 0 else "✗"
        print(f"{status} ({result['tools_found']} tools)")
        results.append(result)

    print_report(results)


if __name__ == "__main__":
    main()

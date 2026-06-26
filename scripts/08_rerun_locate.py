#!/usr/bin/env python3
"""
Script 08: Re-run locate_tools on all cloned repos after the test-path
exclusion fix (tool_locator._is_test_file).

Reads batch_locate_results.jsonl, re-runs locate_tools on every record that
has a clone, and emits a change report + updated JSONL.

Usage:
    python scripts/08_rerun_locate.py [--dry-run] [--out PATH]

    --dry-run   Report changes but do not overwrite batch_locate_results.jsonl
    --out       Alternative JSONL output path (implies dry-run for the original)
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tool_locator import locate_tools

CLONE_DIR  = Path("data/raw/clones/full_batch")
RESULTS_IN = Path("data/processed/batch_locate_results.jsonl")


def repo_slug(repo_url: str) -> str:
    parts = repo_url.rstrip("/").split("/")
    return f"{parts[-2]}__{parts[-1]}"


def rerun_all(dry_run: bool, out_path: Path | None) -> None:
    records = [json.loads(l) for l in RESULTS_IN.open()]
    print(f"Loaded {len(records)} records from {RESULTS_IN}")
    t0 = time.time()

    # Change categories
    ok_to_zero:    list[dict] = []  # N>0  → 0     (pure false positives)
    ok_fewer:      list[dict] = []  # N>0  → 0<M<N (partial; still has tools)
    ok_more:       list[dict] = []  # N>0  → M>N   (test noise was blocking real tools)
    zero_to_ok:    list[dict] = []  # 0    → M>0   (early-exit fix reveals real tools)
    zero_stay:     list[dict] = []  # 0    → 0     (unaffected; regression check)
    unchanged:     list[dict] = []  # N>0  → N     (no test contamination)
    skipped:       int        = 0

    updated_records: list[dict] = []

    for i, rec in enumerate(records, 1):
        url      = rec["repo_url"]
        slug     = repo_slug(url)
        lang     = rec.get("language", "")
        old_n    = rec.get("tools_found", 0)
        rep_rsn  = rec.get("replacement_reason", "")
        is_backup = "is_replacement_for" in rec

        sys.stdout.write(f"\r  {i}/{len(records)}  {slug:<52}")
        sys.stdout.flush()

        if rep_rsn == "clone_failed":
            skipped += 1
            updated_records.append(rec)
            continue

        repo_path = CLONE_DIR / slug
        if not repo_path.exists():
            skipped += 1
            updated_records.append(rec)
            continue

        try:
            tools = locate_tools(repo_path, lang)
        except Exception as exc:
            print(f"\n    ERROR {slug}: {exc}")
            updated_records.append(rec)
            continue

        new_n    = len(tools)
        new_name = tools[0].tool_name if tools else ""
        new_ext  = tools[0].extractor  if tools else ""

        r = dict(rec)  # mutable copy

        if new_n != old_n:
            r["tools_found"]       = new_n
            r["example_tool_name"] = new_name
            r["example_extractor"] = new_ext
            r["test_filter_applied"] = True

            entry = dict(
                slug=slug, lang=lang, is_backup=is_backup,
                bucket=rec.get("star_bucket", ""),
                old=old_n, new=new_n,
                old_ext=rec.get("example_extractor", ""),
                new_ext=new_ext,
            )

            if new_n == 0 and old_n > 0:
                # Completely failed → needs replacement now
                r["replacement_needed"]  = True
                r["replacement_reason"]  = "zero_tools_found"
                r["replacement_notes"]   = (
                    f"test-path filter removed all tools (was {old_n})")
                ok_to_zero.append(entry)

            elif 0 < new_n < old_n:
                ok_fewer.append(entry)

            elif new_n > old_n > 0:
                ok_more.append(entry)

            elif new_n > 0 and old_n == 0:
                # Was zero (failed), now real tools found
                r["replacement_needed"] = False
                r["replacement_notes"]  = (
                    f"test-path filter revealed {new_n} real tool(s) "
                    f"(previously 0 due to early-exit on test file)")
                zero_to_ok.append(entry)

        else:
            if old_n == 0:
                zero_stay.append(slug)
            else:
                unchanged.append(slug)

        updated_records.append(r)

    sys.stdout.write(f"\r  Done in {time.time()-t0:.1f}s{' '*60}\n")

    # ── Report ───────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("RE-RUN LOCATE REPORT — test-path exclusion fix")
    print("=" * 72)
    print(f"Records processed  : {len(records)}   (skipped {skipped}: clone_failed / no clone)")
    print()
    print(f"  Previously >0 tools → ZERO  (false positives removed)   : {len(ok_to_zero)}")
    print(f"  Previously >0 tools → FEWER (mix real + test, now clean) : {len(ok_fewer)}")
    print(f"  Previously >0 tools → MORE  (test noise was blocking)    : {len(ok_more)}")
    print(f"  Previously ZERO     → >0    (early-exit fix recovers)    : {len(zero_to_ok)}")
    print(f"  Previously ZERO     → ZERO  (no regression)              : {len(zero_stay)}")
    print(f"  Unchanged (no test contamination)                        : {len(unchanged)}")

    if ok_to_zero:
        print("\n── Repos losing ALL tools (test-file false positives) ──")
        for c in sorted(ok_to_zero, key=lambda x: (not x["is_backup"], x["slug"])):
            bk = " [backup]" if c["is_backup"] else ""
            print(f"  {c['slug']:<55} {c['lang']}/{c['bucket']}"
                  f"  was {c['old']} ({c['old_ext']}){bk}")

    if ok_fewer:
        print("\n── Repos with PARTIAL tool loss (still have real tools) ──")
        for c in sorted(ok_fewer, key=lambda x: x["slug"]):
            bk = " [B]" if c["is_backup"] else ""
            print(f"  {c['slug']:<55} {c['old']} → {c['new']}{bk}")

    if zero_to_ok:
        print("\n── Previously-zero repos now showing real tools ──")
        for c in sorted(zero_to_ok, key=lambda x: x["slug"]):
            bk = " [B]" if c["is_backup"] else ""
            print(f"  {c['slug']:<55} 0 → {c['new']}  [{c['new_ext']}]{bk}")

    if ok_more:
        print("\n── Repos with MORE tools (test noise was suppressing real tools) ──")
        for c in sorted(ok_more, key=lambda x: -(x["new"] - x["old"]))[:15]:
            bk = " [B]" if c["is_backup"] else ""
            print(f"  {c['slug']:<55} {c['old']} → {c['new']}{bk}")
        if len(ok_more) > 15:
            print(f"  ... and {len(ok_more)-15} more")

    # ── Corrected counts ──────────────────────────────────────────────────────
    # Count directly from updated_records to avoid logic bugs
    primary_ok = sum(
        1 for r in updated_records
        if "is_replacement_for" not in r
        and not r.get("replacement_needed")
        and r.get("tools_found", 0) > 0
    )
    backup_ok = sum(
        1 for r in updated_records
        if "is_replacement_for" in r
        and not r.get("replacement_needed")
        and r.get("tools_found", 0) > 0
    )

    print()
    print("── Corrected extraction counts ──")
    print(f"  Primary repos with ≥1 tool (replacement_needed=False)  : {primary_ok}")
    print(f"  Backup  repos with ≥1 tool (replacement_needed=False)  : {backup_ok}")
    print(f"  Total active repos with ≥1 tool                        : {primary_ok + backup_ok}")
    print(f"  History-walk targets (primary only, per study design)   : {primary_ok}")
    print("=" * 72)

    # ── Write updated JSONL ───────────────────────────────────────────────────
    if not dry_run:
        backup_path = RESULTS_IN.with_suffix(".pre_testfix.jsonl")
        if not backup_path.exists():
            shutil.copy(RESULTS_IN, backup_path)
            print(f"\nOriginal backed up → {backup_path}")

        dest = out_path or RESULTS_IN
        with dest.open("w") as f:
            for r in updated_records:
                f.write(json.dumps(r) + "\n")
        print(f"Updated JSONL written → {dest}")
    else:
        print("\n[--dry-run] JSONL not modified.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out_path = Path(args.out) if args.out else None
    dry_run  = args.dry_run or (out_path is not None)
    rerun_all(dry_run=dry_run, out_path=out_path)


if __name__ == "__main__":
    main()

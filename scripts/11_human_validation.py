#!/usr/bin/env python3
"""
Script 11: Human validation sample for LLM classifier reliability.

Samples 75 events from tool_diffs.jsonl (changed events only), stratified
by structural change type, joins with tool_history_full.jsonl for before/after
state, and exports a CSV for hand-labeling.

Machine labels are intentionally omitted from the export to prevent anchoring.
After the user submits their labels, run this script with --compute-kappa to
merge machine labels and compute Fleiss' Kappa across all three raters
(human, Pass 1, Pass 2).

Usage
─────
  # Generate sample + run classifier on those 75 events:
  python scripts/11_human_validation.py [--seed N]

  # After user fills in human_validation_sample.csv:
  python scripts/11_human_validation.py --compute-kappa

Output (generation mode)
────────────────────────
  data/processed/human_validation_sample.csv      ← give this to the human
  data/processed/human_validation_machine.jsonl   ← machine labels (kept separate)
"""

import argparse
import csv
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

DIFFS_IN      = Path("data/processed/tool_diffs.jsonl")
HISTORY_IN    = Path("data/processed/tool_history_full.jsonl")
SAMPLE_CSV    = Path("data/processed/human_validation_sample.csv")
MACHINE_JSONL = Path("data/processed/human_validation_machine.jsonl")
TARGET_N      = 75

LABELS = ["COSMETIC", "CLARIFICATION", "SCHEMA_EXPANSION",
          "SCHEMA_CONTRACTION", "BEHAVIORAL_DRIFT"]

# ── Change-type proxy (for stratification without pre-classification) ─────────

def _has_change(r: dict) -> bool:
    # Exclude degenerate same-SHA events (tool extracted from multiple source
    # files in the same commit; not a temporal change).
    if r.get("from_sha") == r.get("to_sha"):
        return False
    return bool(
        r.get("description_changed") or
        r.get("schema_fields_added") or
        r.get("schema_fields_removed") or
        r.get("schema_type_changes")
    )


def _change_type(r: dict) -> str:
    desc   = bool(r.get("description_changed"))
    added  = bool(r.get("schema_fields_added"))
    removed = bool(r.get("schema_fields_removed"))
    types  = bool(r.get("schema_type_changes"))

    if types:
        return "type_change"
    if added and removed:
        return "schema_mixed"
    if added and desc:
        return "desc_and_schema_add"
    if removed and desc:
        return "desc_and_schema_remove"
    if added:
        return "schema_add_only"
    if removed:
        return "schema_remove_only"
    return "desc_only"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _props_str(schema: dict) -> str:
    props = schema.get("properties", {})
    if not props:
        return "(none)"
    parts = [f"{n}: {d.get('type', 'any')}" for n, d in props.items()]
    return "{" + ", ".join(parts) + "}"


def _req_str(schema: dict) -> str:
    req = schema.get("required", [])
    return str(req) if req else "[]"


def _change_summary(r: dict) -> str:
    parts = []
    if r.get("description_changed"):
        parts.append("description changed")
    if r.get("schema_fields_added"):
        parts.append(f"fields added: {r['schema_fields_added']}")
    if r.get("schema_fields_removed"):
        parts.append(f"fields removed: {r['schema_fields_removed']}")
    if r.get("schema_type_changes"):
        tc = [f"{c['field']}: {c.get('from_type','?')}→{c.get('to_type','?')}"
              for c in r["schema_type_changes"]]
        parts.append(f"type changes: {tc}")
    return "; ".join(parts)


# ── Snapshot selection ───────────────────────────────────────────────────────

def _pick_snap(snaps: list, side: str, diff: dict) -> dict:
    """
    Given a list of snapshots all sharing the same (repo, tool, sha8), return
    the one that is consistent with the diff record.

    When side='before': the correct snapshot should contain the fields that
    the diff reports as *removed* (they were present before the change).
    When side='after':  the correct snapshot should contain the fields that
    the diff reports as *added* (they appeared after the change).

    Falls back to the last snapshot in the list (matching the original
    last-write-wins behaviour) when heuristics cannot decide.
    """
    if len(snaps) == 1:
        return snaps[0]

    if side == "before":
        target = set(diff.get("schema_fields_removed") or [])
    else:
        target = set(diff.get("schema_fields_added") or [])

    if target:
        for s in snaps:
            props = set(s.get("input_schema", {}).get("properties", {}).keys())
            if target.issubset(props):
                return s

    # Fall back to last entry (stable, matches original index behaviour)
    return snaps[-1]


# ── Sample generation ─────────────────────────────────────────────────────────

def generate(seed: int) -> None:
    random.seed(seed)

    # Load all 2,548 changed events
    changed = []
    for line in DIFFS_IN.open():
        r = json.loads(line)
        if r.get("status") == "drift_event" and _has_change(r):
            changed.append(r)
    print(f"Changed drift events : {len(changed)}")

    # Stratify by proxy change type
    by_type: dict[str, list] = defaultdict(list)
    for r in changed:
        by_type[_change_type(r)].append(r)

    print("\nChange type distribution:")
    for ct, evs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"  {ct:30s}: {len(evs):5d}  ({100*len(evs)/len(changed):.1f}%)")

    # Target allocation: floors guarantee coverage; remainder filled proportionally
    floors = {
        "desc_only":            20,
        "schema_add_only":      12,
        "desc_and_schema_add":  10,
        "schema_remove_only":    8,
        "desc_and_schema_remove": 6,
        "schema_mixed":          6,
        "type_change":           5,
    }
    remaining_target = TARGET_N - sum(floors.values())  # 75 - 67 = 8

    sample = []
    sampled_keys: set[tuple] = set()

    for ct, floor in floors.items():
        pool = by_type.get(ct, [])
        n = min(floor, len(pool))
        chosen = random.sample(pool, n)
        sample.extend(chosen)
        sampled_keys.update((r["repo_url"], r["tool_name"], r["from_sha"]) for r in chosen)

    # Fill remaining 8 proportionally from all types (excluding already sampled)
    if remaining_target > 0:
        leftover = [r for ct in by_type for r in by_type[ct]
                    if (r["repo_url"], r["tool_name"], r["from_sha"]) not in sampled_keys]
        extra = min(remaining_target, len(leftover))
        sample.extend(random.sample(leftover, extra))

    random.shuffle(sample)
    print(f"\nSample size: {len(sample)}")

    # Build snapshot index  {(repo_url, tool_name, sha8) → list[snapshot]}
    # Multi-value because the same tool name can appear in multiple source files
    # within the same commit (e.g. mcp-atlassian's add_comment in jira.py and
    # confluence.py). Storing all snapshots per sha8 lets _pick_snap() select
    # the correct one by matching against the diff's stated field changes.
    print("Loading history index...")
    snap_idx: dict[tuple, list] = defaultdict(list)
    for line in HISTORY_IN.open():
        r = json.loads(line)
        snap_idx[(r["repo_url"], r["tool_name"], r["commit_sha"][:8])].append(r)
    print(f"  {sum(len(v) for v in snap_idx.values())} entries indexed")

    # Write human-facing CSV (no machine labels)
    fields = [
        "event_id", "repo_url", "tool_name", "from_sha", "to_sha",
        "from_date", "to_date", "structural_change_type",
        "before_description", "after_description",
        "schema_fields_added", "schema_fields_removed", "schema_type_changes",
        "before_required", "after_required",
        "before_all_fields", "after_all_fields",
        "human_label", "human_notes",
    ]

    missing_snaps = 0
    rows = []
    for i, d in enumerate(sample):
        key1 = (d["repo_url"], d["tool_name"], d["from_sha"])
        key2 = (d["repo_url"], d["tool_name"], d["to_sha"])
        s1 = _pick_snap(snap_idx.get(key1, [{}]), "before", d)
        s2 = _pick_snap(snap_idx.get(key2, [{}]), "after",  d)
        if not s1 or not s2:
            missing_snaps += 1

        sc1 = s1.get("input_schema", {})
        sc2 = s2.get("input_schema", {})

        rows.append({
            "event_id"               : f"E{i+1:03d}",
            "repo_url"               : d["repo_url"],
            "tool_name"              : d["tool_name"],
            "from_sha"               : d["from_sha"],
            "to_sha"                 : d["to_sha"],
            "from_date"              : d.get("from_date", ""),
            "to_date"                : d.get("to_date", ""),
            "structural_change_type" : _change_type(d),
            "before_description"     : (s1.get("description") or "").strip(),
            "after_description"      : (s2.get("description") or "").strip(),
            "schema_fields_added"    : "; ".join(d.get("schema_fields_added") or []),
            "schema_fields_removed"  : "; ".join(d.get("schema_fields_removed") or []),
            "schema_type_changes"    : "; ".join(
                f"{c['field']}: {c.get('from_type','?')}→{c.get('to_type','?')}"
                for c in (d.get("schema_type_changes") or [])
            ),
            "before_required"        : _req_str(sc1),
            "after_required"         : _req_str(sc2),
            "before_all_fields"      : _props_str(sc1),
            "after_all_fields"       : _props_str(sc2),
            "human_label"            : "",
            "human_notes"            : "",
        })

    with SAMPLE_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"\nHuman CSV written  : {SAMPLE_CSV}  ({len(rows)} rows)")
    if missing_snaps:
        print(f"  WARNING: {missing_snaps} events had missing snapshots in history index")

    # Save event identifiers for classifier run (machine labels step)
    seed_events = [
        {"repo_url": d["repo_url"], "tool_name": d["tool_name"],
         "from_sha": d["from_sha"], "to_sha": d["to_sha"]}
        for d in sample
    ]
    seeds_path = Path("data/processed/human_validation_seeds.json")
    seeds_path.write_text(json.dumps(seed_events, indent=2))
    print(f"Seed events saved  : {seeds_path}")
    print()
    print("Next step: run the classifier on these 75 events to capture machine labels.")
    print("  python scripts/10_llm_classifier.py \\")
    print(f"    --batch {TARGET_N} \\")
    print(f"    --seed-events {seeds_path} \\")
    print(f"    --out {MACHINE_JSONL}")
    print()
    print("Then give the human CSV to the labeler. After labels are returned, run:")
    print("  python scripts/11_human_validation.py --compute-kappa")


# ── Kappa computation ─────────────────────────────────────────────────────────

def compute_kappa() -> None:
    try:
        from sklearn.metrics import cohen_kappa_score
        import numpy as np
    except ImportError:
        print("ERROR: scikit-learn required. Run: pip install scikit-learn")
        sys.exit(1)

    if not SAMPLE_CSV.exists():
        print(f"ERROR: {SAMPLE_CSV} not found. Run without --compute-kappa first.")
        sys.exit(1)
    if not MACHINE_JSONL.exists():
        print(f"ERROR: {MACHINE_JSONL} not found. Run the classifier on the sample first.")
        sys.exit(1)

    # Load human labels
    human_labels: dict[str, str] = {}
    with SAMPLE_CSV.open() as f:
        for row in csv.DictReader(f):
            lbl = row["human_label"].strip().upper()
            if lbl in LABELS:
                human_labels[row["event_id"]] = lbl

    # Load machine labels
    machine: dict[tuple, dict] = {}
    for line in MACHINE_JSONL.open():
        r = json.loads(line)
        key = (r["repo_url"], r["tool_name"], r["from_sha"])
        machine[key] = r

    # Reload event_id → (repo, tool, sha) mapping
    id_map: dict[str, tuple] = {}
    with SAMPLE_CSV.open() as f:
        for row in csv.DictReader(f):
            id_map[row["event_id"]] = (row["repo_url"], row["tool_name"], row["from_sha"])

    # Build aligned label vectors
    ids_with_all: list[str] = []
    h_labels, p1_labels, p2_labels = [], [], []

    for eid, key in id_map.items():
        if eid not in human_labels:
            continue
        m = machine.get(key)
        if m is None:
            continue
        p1 = m.get("pass1_label", "").strip().upper()
        p2 = m.get("pass2_label", "").strip().upper()
        if p1 not in LABELS or p2 not in LABELS:
            continue
        ids_with_all.append(eid)
        h_labels.append(human_labels[eid])
        p1_labels.append(p1)
        p2_labels.append(p2)

    n = len(ids_with_all)
    print(f"Events with all 3 labels: {n}")
    if n == 0:
        print("No events have all three labels. Cannot compute kappa.")
        sys.exit(1)

    k_h_p1  = cohen_kappa_score(h_labels, p1_labels)
    k_h_p2  = cohen_kappa_score(h_labels, p2_labels)
    k_p1_p2 = cohen_kappa_score(p1_labels, p2_labels)

    print(f"\nCohen's Kappa (pairwise):")
    print(f"  Human vs Pass 1 : {k_h_p1:.3f}")
    print(f"  Human vs Pass 2 : {k_h_p2:.3f}")
    print(f"  Pass 1 vs Pass 2: {k_p1_p2:.3f}")

    # Fleiss' Kappa (3 raters, 5 categories)
    label_to_idx = {l: i for i, l in enumerate(LABELS)}
    n_cats = len(LABELS)
    rating_matrix = np.zeros((n, n_cats), dtype=int)
    for i, (h, p1, p2) in enumerate(zip(h_labels, p1_labels, p2_labels)):
        rating_matrix[i, label_to_idx[h]]  += 1
        rating_matrix[i, label_to_idx[p1]] += 1
        rating_matrix[i, label_to_idx[p2]] += 1

    # Fleiss' Kappa formula
    N = n          # subjects
    k = n_cats     # categories
    n_r = 3        # raters per subject

    P_i = (1 / (n_r * (n_r - 1))) * (
        (rating_matrix ** 2).sum(axis=1) - n_r
    )
    P_bar = P_i.mean()

    p_j = rating_matrix.sum(axis=0) / (N * n_r)
    P_e  = (p_j ** 2).sum()

    fleiss_k = (P_bar - P_e) / (1 - P_e) if (1 - P_e) != 0 else float("nan")
    print(f"\nFleiss' Kappa (3 raters): {fleiss_k:.3f}")

    # Label distribution per rater
    print("\nLabel distribution:")
    print(f"  {'Label':22s}  {'Human':6s}  {'Pass1':6s}  {'Pass2':6s}")
    from collections import Counter
    ch, cp1, cp2 = Counter(h_labels), Counter(p1_labels), Counter(p2_labels)
    for lbl in LABELS:
        print(f"  {lbl:22s}  {ch[lbl]:6d}  {cp1[lbl]:6d}  {cp2[lbl]:6d}")

    # Save merged results
    merged_path = Path("data/processed/human_validation_merged.csv")
    merged_fields = [
        "event_id", "repo_url", "tool_name", "from_sha",
        "human_label", "pass1_label", "pass2_label",
        "h_p1_agree", "h_p2_agree", "p1_p2_agree", "all_agree",
    ]
    with merged_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=merged_fields)
        w.writeheader()
        for eid, h, p1, p2 in zip(ids_with_all, h_labels, p1_labels, p2_labels):
            key = id_map[eid]
            w.writerow({
                "event_id"   : eid,
                "repo_url"   : key[0],
                "tool_name"  : key[1],
                "from_sha"   : key[2],
                "human_label": h,
                "pass1_label": p1,
                "pass2_label": p2,
                "h_p1_agree" : h == p1,
                "h_p2_agree" : h == p2,
                "p1_p2_agree": p1 == p2,
                "all_agree"  : h == p1 == p2,
            })
    print(f"\nMerged results     : {merged_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42,
                    help="Random seed for reproducible sampling (default 42)")
    ap.add_argument("--compute-kappa", action="store_true",
                    help="Merge human labels and compute kappa (post-labeling step)")
    args = ap.parse_args()

    if args.compute_kappa:
        compute_kappa()
    else:
        generate(args.seed)


if __name__ == "__main__":
    main()

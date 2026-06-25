#!/usr/bin/env python3
"""
Script 03 — Tag exclusion reasons, draw stratified primary sample, build backup pool.

Reads : data/processed/seed_servers_enriched.csv
Writes:
  data/processed/seed_servers_enriched.csv   (adds exclusion_reason column)
  data/processed/sample_primary_380.csv
  data/processed/sample_backup_pool.csv

Stratification dimensions
  language   : gh_language from GitHub API (NaN → "(unknown)")
  star_bucket: [10–49] [50–199] [200–999] [1000+]

Proportional allocation uses the largest-remainder method so the total
comes out to exactly PRIMARY_N. Strata with fewer repos than their
allocation receive all available repos; the shortfall is redistributed
proportionally across the remaining uncapped strata (iterated until stable).

Reproducibility: RANDOM_SEED is printed and stored in each output CSV as
a header comment row so downstream scripts can verify the same draw.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ENRICHED_CSV   = REPO_ROOT / "data" / "processed" / "seed_servers_enriched.csv"
PRIMARY_CSV    = REPO_ROOT / "data" / "processed" / "sample_primary_380.csv"
BACKUP_CSV     = REPO_ROOT / "data" / "processed" / "sample_backup_pool.csv"

PRIMARY_N      = 380
CUTOFF_MONTHS  = 12
RANDOM_SEED    = 42

STAR_BINS      = [10, 50, 200, 1000, np.inf]
STAR_LABELS    = ["10-49", "50-199", "200-999", "1000+"]


# ── exclusion reason ──────────────────────────────────────────────────────────

def compute_exclusion_reason(df: pd.DataFrame, cutoff: datetime) -> pd.Series:
    """
    Return a Series of exclusion reason strings.
    Repos with status != "ok" have NaN for all API fields; they are
    treated as failing low_stars and stale (cannot be confirmed eligible).
    """
    pushed_dt = pd.to_datetime(df["pushed_at"], utc=True, errors="coerce")

    fail_stars    = df["gh_stars"].isna() | (df["gh_stars"] < 10)
    fail_archived = df["archived"].fillna(False).astype(bool)
    fail_stale    = pushed_dt.isna() | (pushed_dt < cutoff)

    reasons = []
    for fs, fa, fst in zip(fail_stars, fail_archived, fail_stale):
        parts = []
        if fs:  parts.append("low_stars")
        if fa:  parts.append("archived")
        if fst: parts.append("stale")
        reasons.append("+".join(parts) if parts else "eligible")

    return pd.Series(reasons, index=df.index, name="exclusion_reason")


# ── star bucket helper ────────────────────────────────────────────────────────

def star_bucket(stars: pd.Series) -> pd.Series:
    return pd.cut(
        stars,
        bins=STAR_BINS,
        labels=STAR_LABELS,
        right=False,       # intervals are [lo, hi)
    ).astype(str)


# ── proportional allocation (largest-remainder with cap iteration) ────────────

def proportional_allocate(counts: dict[str, int], total: int) -> dict[str, int]:
    """
    Allocate *total* samples proportionally across strata.
    Uses largest-remainder rounding; iterates to handle strata smaller
    than their raw allocation.
    """
    remaining_total = total
    remaining_counts = dict(counts)
    allocation: dict[str, int] = {}

    while True:
        n_pool = sum(remaining_counts.values())
        if n_pool == 0 or remaining_total == 0:
            for k in remaining_counts:
                allocation[k] = allocation.get(k, 0)
            break

        # raw proportional allocation
        raw = {k: v / n_pool * remaining_total for k, v in remaining_counts.items()}
        floors = {k: int(v) for k, v in raw.items()}
        leftover = remaining_total - sum(floors.values())

        # distribute leftover to largest fractional parts
        fracs = sorted(raw.items(), key=lambda x: -(x[1] - int(x[1])))
        for i, (k, _) in enumerate(fracs):
            floors[k] += 1 if i < leftover else 0

        # cap at available and find overage
        capped: dict[str, int] = {}
        overage = 0
        free: dict[str, int] = {}
        for k, alloc in floors.items():
            avail = remaining_counts[k]
            if alloc >= avail:
                capped[k] = avail
                overage += alloc - avail
            else:
                free[k] = alloc

        # merge capped into final allocation
        for k, v in capped.items():
            allocation[k] = allocation.get(k, 0) + v

        if overage == 0:
            for k, v in free.items():
                allocation[k] = allocation.get(k, 0) + v
            break

        # iterate: redistribute overage among uncapped strata
        remaining_total = sum(free.values()) + overage
        remaining_counts = {k: remaining_counts[k] - free[k] for k in free}

    return allocation


# ── sampling ──────────────────────────────────────────────────────────────────

def draw_sample(eligible: pd.DataFrame, n: int, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Return (primary_sample, backup_pool).
    primary_sample : n rows, proportionally stratified
    backup_pool    : remaining eligible rows, ranked by stars desc within stratum
    """
    eligible = eligible.copy()
    eligible["_stratum"] = eligible["gh_language_clean"] + " | " + eligible["star_bucket"]

    strata_counts = eligible["_stratum"].value_counts().to_dict()
    allocation    = proportional_allocate(strata_counts, n)

    primary_parts = []
    backup_parts  = []

    for stratum, group in eligible.groupby("_stratum"):
        n_take = allocation.get(stratum, 0)
        if n_take == 0:
            backup_parts.append(group)
            continue
        shuffled = group.sample(frac=1, random_state=int(rng.integers(2**31)))
        primary_parts.append(shuffled.iloc[:n_take])
        backup_parts.append(shuffled.iloc[n_take:])

    primary = pd.concat(primary_parts, ignore_index=True)
    backup  = pd.concat(backup_parts,  ignore_index=True)

    # rank backup within stratum by stars desc (best replacement first)
    backup = backup.sort_values(["_stratum", "gh_stars"], ascending=[True, False])
    backup["backup_rank"] = backup.groupby("_stratum").cumcount() + 1

    return primary, backup


# ── printing ──────────────────────────────────────────────────────────────────

def print_exclusion_table(df: pd.DataFrame) -> None:
    counts = df["exclusion_reason"].value_counts().sort_values(ascending=False)
    print(f"\n{'═'*52}")
    print(f"  1. EXCLUSION REASON BREAKDOWN  (n={len(df):,})")
    print(f"{'═'*52}")
    print(f"  {'Reason':<30} {'Count':>6}  {'%':>6}")
    print(f"  {'-'*30} {'-'*6}  {'-'*6}")
    for reason, cnt in counts.items():
        print(f"  {reason:<30} {cnt:>6,}  {100*cnt/len(df):>5.1f}%")
    print(f"  {'─'*30} {'─'*6}")
    print(f"  {'TOTAL':<30} {len(df):>6,}  100.0%")
    eligible_n = (df["exclusion_reason"] == "eligible").sum()
    print(f"\n  ✓ eligible rows : {eligible_n:,}")
    print(f"  ✓ total rows    : {len(df):,}")


def print_stratification_table(sample: pd.DataFrame, label: str) -> None:
    pivot = (
        sample.groupby(["gh_language_clean", "star_bucket"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    # ensure all bucket columns present
    for col in STAR_LABELS:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot[STAR_LABELS]
    pivot["TOTAL"] = pivot.sum(axis=1)
    pivot.loc["TOTAL"] = pivot.sum()

    width = 10
    print(f"\n{'═'*62}")
    print(f"  {label}  (n={len(sample):,})")
    print(f"{'═'*62}")
    header = f"  {'Language':<18}" + "".join(f"{b:>{width}}" for b in STAR_LABELS) + f"{'TOTAL':>{width}}"
    print(header)
    print(f"  {'-'*18}" + "-"*(width * (len(STAR_LABELS)+1)))
    for lang, row in pivot.iterrows():
        cells = "".join(f"{int(row[b]):>{width}}" for b in STAR_LABELS)
        total = f"{int(row['TOTAL']):>{width}}"
        marker = " ←" if lang == "TOTAL" else ""
        print(f"  {str(lang):<18}{cells}{total}{marker}")


def print_backup_strata(backup: pd.DataFrame) -> None:
    counts = backup.groupby("_stratum").size().reset_index(name="backup_count")
    counts = counts.sort_values("backup_count", ascending=False)
    print(f"\n{'═'*52}")
    print(f"  3. BACKUP POOL PER STRATUM  (n={len(backup):,} total)")
    print(f"{'═'*52}")
    print(f"  {'Stratum':<35} {'Backup':>6}")
    print(f"  {'-'*35} {'-'*6}")
    for _, row in counts.iterrows():
        print(f"  {row['_stratum']:<35} {row['backup_count']:>6,}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    today  = datetime.now(timezone.utc)
    cutoff = today - relativedelta(months=CUTOFF_MONTHS)

    # ── load and tag ─────────────────────────────────────────────────────────
    df = pd.read_csv(ENRICHED_CSV)
    df["exclusion_reason"] = compute_exclusion_reason(df, cutoff)
    df.to_csv(ENRICHED_CSV, index=False)
    print(f"Updated {ENRICHED_CSV.name} with exclusion_reason column.")

    print_exclusion_table(df)

    # ── prepare eligible subset ───────────────────────────────────────────────
    eligible = df[df["exclusion_reason"] == "eligible"].copy()
    eligible["gh_language_clean"] = eligible["gh_language"].fillna("(unknown)")
    eligible["star_bucket"]       = star_bucket(eligible["gh_stars"])

    print(f"\n  Eligible pool: {len(eligible):,} repos")
    print(f"  Random seed  : {RANDOM_SEED}")
    print(f"  Cutoff date  : {cutoff.date()}")

    # ── draw sample ───────────────────────────────────────────────────────────
    rng = np.random.default_rng(RANDOM_SEED)
    primary, backup = draw_sample(eligible, PRIMARY_N, rng)

    assert len(primary) == PRIMARY_N, f"Expected {PRIMARY_N} primary rows, got {len(primary)}"

    # ── save ──────────────────────────────────────────────────────────────────
    primary.drop(columns=["_stratum"]).to_csv(PRIMARY_CSV, index=False)
    backup.drop(columns=["_stratum"]).to_csv(BACKUP_CSV, index=False)
    print(f"\n  Saved → {PRIMARY_CSV.name}  ({len(primary):,} rows)")
    print(f"  Saved → {BACKUP_CSV.name} ({len(backup):,} rows)")

    # ── print tables ──────────────────────────────────────────────────────────
    print_stratification_table(primary, "2. PRIMARY SAMPLE STRATIFICATION")
    print_backup_strata(backup)
    print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Script 02 — Enrich seed repos with live GitHub metadata and print pool-size analysis.

Fetches for every repo in data/processed/seed_servers.csv:
  pushed_at, stargazers_count, archived, fork, size, default_branch, visibility

Saves:
  data/raw/github_repo_metadata.jsonl       one JSON record per repo (checkpoint)
  data/processed/seed_servers_enriched.csv  seed data + GitHub fields merged

Then prints the pool-size funnel for the two filters planned for sampling:
  (1) stargazers_count >= 10
  (2) pushed_at within the last 24 months (proxy for "commit in last 24 months")

Requires: GITHUB_TOKEN in .env  (5,000 req/hr authenticated vs 60 unauthenticated)

Usage:
    python scripts/02_enrich_github_metadata.py
    python scripts/02_enrich_github_metadata.py --limit 50     # test run
    python scripts/02_enrich_github_metadata.py --force-refetch
    python scripts/02_enrich_github_metadata.py --analysis-only  # skip fetch, reuse cache
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
from dotenv import load_dotenv
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

# ── paths ─────────────────────────────────────────────────────────────────────
SEED_CSV        = REPO_ROOT / "data" / "processed" / "seed_servers.csv"
CHECKPOINT_FILE = REPO_ROOT / "data" / "raw"       / "github_repo_metadata.jsonl"
ENRICHED_CSV    = REPO_ROOT / "data" / "processed" / "seed_servers_enriched.csv"

# Filter thresholds (matching Prompt 4 sampling criteria)
MIN_STARS    = 10
CUTOFF_MONTHS = 24  # "commit in last N months"

GITHUB_API = "https://api.github.com"


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _build_session() -> tuple[requests.Session, bool]:
    """Return a requests.Session with auth headers if GITHUB_TOKEN is set."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    session = requests.Session()
    session.headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    authenticated = bool(token)
    if authenticated:
        session.headers["Authorization"] = f"Bearer {token}"
    return session, authenticated


def _check_rate_limit(response: requests.Response) -> None:
    """Sleep if we're about to exhaust the GitHub rate limit."""
    remaining = int(response.headers.get("X-RateLimit-Remaining", 100))
    if remaining < 5:
        reset_ts  = int(response.headers.get("X-RateLimit-Reset", time.time() + 65))
        sleep_sec = max(0, reset_ts - time.time()) + 2
        tqdm.write(f"\n  [rate-limit] {remaining} requests left — sleeping {sleep_sec:.0f}s until reset …")
        time.sleep(sleep_sec)


def _parse_owner_repo(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a github.com URL, or return None."""
    try:
        parts = urlparse(url).path.strip("/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception:
        pass
    return None


def fetch_repo_metadata(repo_url: str, session: requests.Session) -> dict:
    """
    Fetch metadata for one GitHub repo.
    Returns a dict with keys: repo_url, status, and (if found) the API fields.
    status is one of: "ok", "not_found", "rate_limited", "error", "non_github"
    """
    parsed = _parse_owner_repo(repo_url)
    if parsed is None:
        return {"repo_url": repo_url, "status": "non_github"}

    owner, repo = parsed
    url = f"{GITHUB_API}/repos/{owner}/{repo}"

    try:
        resp = session.get(url, timeout=30)
        _check_rate_limit(resp)

        if resp.status_code == 200:
            data = resp.json()
            return {
                "repo_url":          repo_url,
                "status":            "ok",
                "pushed_at":         data.get("pushed_at"),          # ISO-8601 string
                "gh_stars":          data.get("stargazers_count"),
                "archived":          data.get("archived", False),
                "fork":              data.get("fork", False),
                "size_kb":           data.get("size", 0),            # kilobytes
                "default_branch":    data.get("default_branch"),
                "visibility":        data.get("visibility", "public"),
                "open_issues":       data.get("open_issues_count", 0),
                "created_at":        data.get("created_at"),
                "gh_language":       data.get("language"),           # GitHub's detected primary language
            }
        elif resp.status_code == 404:
            return {"repo_url": repo_url, "status": "not_found"}
        elif resp.status_code == 403:
            # Usually means rate-limited or access forbidden
            return {"repo_url": repo_url, "status": "rate_limited"}
        else:
            return {"repo_url": repo_url, "status": f"http_{resp.status_code}"}

    except requests.RequestException as exc:
        return {"repo_url": repo_url, "status": f"error:{exc}"}


# ── Checkpoint I/O ────────────────────────────────────────────────────────────

def load_checkpoint() -> dict[str, dict]:
    """Load existing JSONL checkpoint into a {repo_url: record} dict."""
    cache: dict[str, dict] = {}
    if not CHECKPOINT_FILE.exists():
        return cache
    with CHECKPOINT_FILE.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    cache[rec["repo_url"]] = rec
                except json.JSONDecodeError:
                    pass
    return cache


def append_checkpoint(record: dict) -> None:
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT_FILE.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


# ── Fetch loop ────────────────────────────────────────────────────────────────

def fetch_all(repo_urls: list[str], force_refetch: bool, limit: int | None) -> dict[str, dict]:
    session, authenticated = _build_session()

    if not authenticated:
        print(
            "\n  [WARN] No GITHUB_TOKEN found in .env. Rate limit is 60 req/hr "
            f"unauthenticated, which would take ~{len(repo_urls)//60 + 1}h for "
            f"{len(repo_urls):,} repos.\n"
            "  Add GITHUB_TOKEN=<token> to your .env file and re-run.\n"
            "  Continuing anyway (useful for small --limit runs) …\n"
        )

    cache = {} if force_refetch else load_checkpoint()
    todo  = [u for u in repo_urls if u not in cache]

    if limit is not None:
        todo = todo[:limit]

    if not todo:
        print(f"  All {len(repo_urls):,} repos already in checkpoint — skipping fetch.")
        return cache

    print(f"  Fetching {len(todo):,} repos ({len(cache):,} already cached) …")

    for url in tqdm(todo, unit="repo", dynamic_ncols=True):
        record = fetch_repo_metadata(url, session)
        cache[url]= record
        append_checkpoint(record)

    return cache


# ── Analysis ──────────────────────────────────────────────────────────────────

def _cutoff_date() -> datetime:
    """Return the datetime CUTOFF_MONTHS ago from today (UTC)."""
    from dateutil.relativedelta import relativedelta  # type: ignore[import]
    return datetime.now(timezone.utc) - relativedelta(months=CUTOFF_MONTHS)


def run_analysis(df: pd.DataFrame) -> None:
    """
    Print pool-size funnel using the two planned sampling filters.
    df must have columns: gh_stars, pushed_at, archived, fork, status
    """
    today = datetime.now(timezone.utc)
    cutoff = _cutoff_date()
    n_total = len(df)

    divider = "─" * 65
    print(f"\n{divider}")
    print(f"  POOL-SIZE ANALYSIS  (n={n_total:,} seed repos, today={today.date()})")
    print(divider)

    # ── Status breakdown ──────────────────────────────────────────────────────
    status_counts = df["status"].value_counts()
    print("\n  GitHub API status:")
    for status, count in status_counts.items():
        print(f"    {status:<25} {count:>5,}  ({100*count/n_total:.1f}%)")

    # Restrict further analysis to repos that resolved successfully
    live = df[df["status"] == "ok"].copy()
    n_live = len(live)
    print(f"\n  Reachable repos (status=ok): {n_live:,} of {n_total:,}")

    # ── Stars breakdown (on live repos) ──────────────────────────────────────
    print(f"\n  Stars breakdown  (across {n_live:,} reachable repos):")
    stars = live["gh_stars"].fillna(0).astype(int)
    bands = [
        ("0",           (stars == 0)),
        ("1 – 4",       (stars >= 1)  & (stars <= 4)),
        ("5 – 9",       (stars >= 5)  & (stars <= 9)),
        (f"≥ {MIN_STARS} (threshold)", stars >= MIN_STARS),
    ]
    for label, mask in bands:
        n = mask.sum()
        print(f"    {label:<30} {n:>5,}  ({100*n/n_live:.1f}%)")

    # ── Activity breakdown ────────────────────────────────────────────────────
    live["pushed_dt"] = pd.to_datetime(live["pushed_at"], utc=True, errors="coerce")
    active_mask    = live["pushed_dt"] >= cutoff
    inactive_mask  = live["pushed_dt"] <  cutoff
    no_push_mask   = live["pushed_dt"].isna()

    print(f"\n  Activity breakdown  (pushed_at proxy; cutoff = {cutoff.date()}):")
    print(f"    pushed within {CUTOFF_MONTHS} months    {active_mask.sum():>5,}  ({100*active_mask.sum()/n_live:.1f}%)")
    print(f"    pushed > {CUTOFF_MONTHS} months ago     {inactive_mask.sum():>5,}  ({100*inactive_mask.sum()/n_live:.1f}%)")
    print(f"    no push date recorded      {no_push_mask.sum():>5,}  ({100*no_push_mask.sum()/n_live:.1f}%)")

    # ── Archived / fork flags ─────────────────────────────────────────────────
    n_archived = live["archived"].fillna(False).astype(bool).sum()
    n_fork     = live["fork"].fillna(False).astype(bool).sum()
    print(f"\n  Other flags  (on {n_live:,} reachable repos):")
    print(f"    archived                   {n_archived:>5,}  ({100*n_archived/n_live:.1f}%)")
    print(f"    fork                       {n_fork:>5,}  ({100*n_fork/n_live:.1f}%)")

    # ── Combined filter ───────────────────────────────────────────────────────
    combined = (
        (stars >= MIN_STARS) &
        active_mask &
        ~live["archived"].fillna(False).astype(bool)
    )
    n_pool = combined.sum()

    print(f"\n{divider}")
    print(f"  COMBINED FILTER: stars ≥ {MIN_STARS}  AND  pushed within {CUTOFF_MONTHS} months  AND  not archived")
    print(f"  Qualifying pool:  {n_pool:,} repos  ({100*n_pool/n_total:.1f}% of {n_total:,} seed repos)")
    print(divider)

    # ── Pool language breakdown ───────────────────────────────────────────────
    pool_df = live[combined]
    lang_counts = pool_df["gh_language"].fillna("(unknown)").value_counts()
    print(f"\n  Pool language breakdown (top 8, n={n_pool:,}):")
    for lang, count in lang_counts.head(8).items():
        print(f"    {lang:<30} {count:>4,}  ({100*count/n_pool:.1f}%)")

    # ── Pool stars percentiles ────────────────────────────────────────────────
    pool_stars = pool_df["gh_stars"].dropna().astype(int)
    print(f"\n  Pool stars distribution (n={len(pool_stars):,}):")
    for pct in [25, 50, 75, 90, 99]:
        val = pool_stars.quantile(pct / 100)
        print(f"    p{pct:<3}  {val:>6.0f}")
    print(f"    max   {pool_stars.max():>6,}")

    print(divider)


# ── Merge + save ──────────────────────────────────────────────────────────────

def merge_and_save(seed_df: pd.DataFrame, cache: dict[str, dict]) -> pd.DataFrame:
    meta_df = pd.DataFrame(list(cache.values()))
    merged  = seed_df.merge(meta_df, on="repo_url", how="left")
    merged.to_csv(ENRICHED_CSV, index=False)
    print(f"\n  Saved enriched data → {ENRICHED_CSV.relative_to(REPO_ROOT)}")
    return merged


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force-refetch",  action="store_true", help="Ignore checkpoint, re-fetch all repos.")
    p.add_argument("--analysis-only",  action="store_true", help="Skip fetch step, use existing checkpoint.")
    p.add_argument("--limit", type=int, default=None, metavar="N", help="Fetch at most N repos (for testing).")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    seed_df = pd.read_csv(SEED_CSV)
    repo_urls = seed_df["repo_url"].dropna().tolist()
    print(f"Loaded {len(repo_urls):,} repos from {SEED_CSV.relative_to(REPO_ROOT)}")

    if args.analysis_only:
        cache = load_checkpoint()
        print(f"  --analysis-only: loaded {len(cache):,} cached records.")
    else:
        cache = fetch_all(repo_urls, force_refetch=args.force_refetch, limit=args.limit)

    merged = merge_and_save(seed_df, cache)

    # Only run analysis when we have enough data (at least 80% fetched)
    n_ok = sum(1 for v in cache.values() if v.get("status") == "ok")
    coverage = n_ok / len(repo_urls) if repo_urls else 0
    if coverage < 0.80 and not args.analysis_only:
        print(
            f"\n  [INFO] Only {n_ok:,}/{len(repo_urls):,} repos fetched so far ({coverage:.0%}). "
            "Re-run to complete, then run --analysis-only for final numbers."
        )
        return

    try:
        run_analysis(merged)
    except ImportError:
        print("\n  [ERROR] python-dateutil is required for analysis. Run: pip install python-dateutil")


if __name__ == "__main__":
    main()

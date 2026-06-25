#!/usr/bin/env python3
"""
Script 01 — Load and merge seed MCP server datasets.

Produces: data/processed/seed_servers.csv

────────────────────────────────────────────────────────────────────────────
SOURCE A  (auto-downloaded)
  "MCP at First Glance" — arXiv:2506.13538
  Repo   : SAILResearch/replication-25-mcp-server-empirical-study
  File   : all_mcp_servers.csv
  Schema : entity_name, integration_type, github_repo_link, star_count, language
  Size   : ~1,899 entries
────────────────────────────────────────────────────────────────────────────
SOURCE B  (manual placement required)
  MCPCrawler dataset — arXiv:2509.25292
  Repo   : zhuaiballl/mcp_collection  — PRIVATE as of June 2026.

  ── MANUAL DOWNLOAD INSTRUCTIONS ─────────────────────────────────────────
  When/if the repo becomes public (watch https://github.com/zhuaiballl/mcp_collection):
    1. Download the server list CSV from the repo.
    2. Place it at:  data/raw/mcpcrawler_mcp_collection.csv
    3. Expected columns (from paper description):
         url        — full GitHub URL
         language   — primary programming language
         stars      — GitHub star count
       (exact column names TBC; edit MCPCRAWLER_COL_MAP below to match)
    4. Re-run this script; it will detect the file automatically.
  ──────────────────────────────────────────────────────────────────────────
  ALTERNATIVE: MCPCorpus (arXiv:2506.23474)
    Repo: https://github.com/Snakinya/MCPCorpus  (~14K servers, JSON format)
    1. Download Crawler/Servers/ JSON files from that repo.
    2. Concatenate into a single JSON array and save as:
         data/raw/mcpcorpus_servers.json
    3. Pass --mcpcorpus to this script.
────────────────────────────────────────────────────────────────────────────

Usage:
    python scripts/01_load_seed_data.py
    python scripts/01_load_seed_data.py --force-redownload
    python scripts/01_load_seed_data.py --mcpcorpus          # enable MCPCorpus source
    python scripts/01_load_seed_data.py --no-validate        # skip Pydantic row validation
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

# ── make src/ importable when running as a script ───────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.schemas import ServerSample  # noqa: E402  (import after sys.path patch)

# ── paths ────────────────────────────────────────────────────────────────────
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

SAIL_RAW_PATH = RAW_DIR / "sail_all_mcp_servers.csv"
MCPCRAWLER_PATH = RAW_DIR / "mcpcrawler_mcp_collection.csv"
MCPCORPUS_PATH = RAW_DIR / "mcpcorpus_servers.json"
OUTPUT_PATH = PROCESSED_DIR / "seed_servers.csv"

# ── remote URLs ───────────────────────────────────────────────────────────────
SAIL_URL = (
    "https://raw.githubusercontent.com/"
    "SAILResearch/replication-25-mcp-server-empirical-study/main/all_mcp_servers.csv"
)

# Column name mapping for MCPCrawler if/when it becomes public.
# Edit the VALUES here to match the actual column names in the downloaded file.
MCPCRAWLER_COL_MAP: dict[str, str] = {
    "url": "github_repo_link",       # column holding full GitHub URL → canonical name
    "language": "language",
    "stars": "star_count",
}


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _download(url: str, dest: Path, desc: str) -> None:
    """Stream-download *url* to *dest*, showing a tqdm progress bar."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    total = int(response.headers.get("content-length", 0)) or None
    with dest.open("wb") as fh, tqdm(
        desc=f"Downloading {desc}",
        total=total,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for chunk in response.iter_content(chunk_size=8192):
            fh.write(chunk)
            bar.update(len(chunk))


def _normalise_github_url(raw: str) -> str | None:
    """Return a canonical https://github.com/owner/repo URL, or None if unparseable."""
    if not isinstance(raw, str):
        return None
    url = raw.strip().rstrip("/")
    if not url:
        return None
    # Upgrade http → https
    if url.startswith("http://github.com"):
        url = "https" + url[4:]
    # Accept bare "owner/repo" form
    if not url.startswith("http") and url.count("/") == 1:
        url = f"https://github.com/{url}"
    return url


def _validate_rows(df: pd.DataFrame, source_label: str) -> pd.DataFrame:
    """
    Attempt to construct a ServerSample for every row; drop and report failures.
    Returns the subset of rows that passed validation.
    """
    valid_indices = []
    failures: list[tuple[int, str]] = []

    for idx, row in df.iterrows():
        try:
            # pandas uses float NaN for missing string cells; convert to None so
            # Optional[str] fields pass Pydantic validation instead of failing with
            # "Input should be a valid string [input_value=nan]".
            def _opt(v):
                return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v

            ServerSample(
                repo_url=row["repo_url"],
                registry_source=row["registry_source"],
                category=_opt(row.get("category")),
                language=_opt(row.get("language")),
                stars=_opt(row.get("stars")),
                last_commit_date=_opt(row.get("last_commit_date")),
            )
            valid_indices.append(idx)
        except Exception as exc:
            failures.append((idx, str(exc)))

    if failures:
        print(
            f"  [WARN] {source_label}: {len(failures)} row(s) failed Pydantic "
            f"validation and were dropped."
        )
        for i, (idx, msg) in enumerate(failures[:5]):  # show at most 5 examples
            print(f"    row {idx}: {msg}")
        if len(failures) > 5:
            print(f"    ... and {len(failures) - 5} more.")

    return df.loc[valid_indices].reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# Source loaders
# Each returns a DataFrame with these canonical columns:
#   repo_url, registry_source, category, language, stars, last_commit_date
# ────────────────────────────────────────────────────────────────────────────

def load_sail_research(force_redownload: bool = False) -> pd.DataFrame:
    """
    Load the SAIL Research replication dataset (arXiv:2506.13538).
    Downloads automatically on first run; cached in data/raw/.
    """
    if not SAIL_RAW_PATH.exists() or force_redownload:
        print("Source A (SAIL Research): downloading all_mcp_servers.csv …")
        _download(SAIL_URL, SAIL_RAW_PATH, "SAIL all_mcp_servers.csv")
    else:
        print(f"Source A (SAIL Research): using cached {SAIL_RAW_PATH.name}")

    df = pd.read_csv(SAIL_RAW_PATH)

    # Rename to canonical schema
    df = df.rename(columns={
        "github_repo_link": "repo_url",
        "star_count": "stars",
        "integration_type": "category",   # mined / official / community
    })

    df["repo_url"] = df["repo_url"].map(_normalise_github_url)
    df["registry_source"] = "mcp-first-glance"
    df["last_commit_date"] = pd.NaT

    # Keep only the canonical columns
    canonical = ["repo_url", "registry_source", "category", "language", "stars", "last_commit_date"]
    df = df[[c for c in canonical if c in df.columns]]

    # stars: coerce to nullable integer (some entries may be NaN)
    df["stars"] = pd.to_numeric(df["stars"], errors="coerce").astype("Int64")

    print(f"  Loaded {len(df):,} rows from Source A.")
    return df


def load_mcpcrawler() -> pd.DataFrame | None:
    """
    Load the MCPCrawler dataset (arXiv:2509.25292) from a manually placed file.
    Returns None and prints instructions if the file is not present.
    """
    if not MCPCRAWLER_PATH.exists():
        print(textwrap.dedent(f"""
            Source B (MCPCrawler / arXiv:2509.25292): NOT FOUND — skipping.

            The repo zhuaiballl/mcp_collection is currently private (verified June 2026).
            When it becomes public:
              1. Download the server-list CSV.
              2. Save it to:  {MCPCRAWLER_PATH}
              3. Edit MCPCRAWLER_COL_MAP in this script if column names differ.
              4. Re-run this script.
        """).strip())
        return None

    df = pd.read_csv(MCPCRAWLER_PATH)

    # Rename using the editable column map (handles whatever names the file uses)
    rename = {k: v for k, v in MCPCRAWLER_COL_MAP.items() if k in df.columns}
    df = df.rename(columns=rename)

    if "github_repo_link" not in df.columns:
        raise ValueError(
            f"MCPCrawler file {MCPCRAWLER_PATH} has no recognisable URL column. "
            "Update MCPCRAWLER_COL_MAP to map the URL column name."
        )

    df = df.rename(columns={"github_repo_link": "repo_url", "star_count": "stars"})
    df["repo_url"] = df["repo_url"].map(_normalise_github_url)
    df["registry_source"] = "mcpcrawler-2509.25292"
    df["category"] = df.get("category", pd.NA)
    df["last_commit_date"] = pd.NaT

    canonical = ["repo_url", "registry_source", "category", "language", "stars", "last_commit_date"]
    df = df[[c for c in canonical if c in df.columns]]
    df["stars"] = pd.to_numeric(df["stars"], errors="coerce").astype("Int64")

    print(f"  Loaded {len(df):,} rows from Source B (MCPCrawler).")
    return df


def load_mcpcorpus() -> pd.DataFrame | None:
    """
    Load MCPCorpus (arXiv:2506.23474, https://github.com/Snakinya/MCPCorpus).
    Expects a JSON array at data/raw/mcpcorpus_servers.json.
    Returns None and prints instructions if the file is not present.
    """
    if not MCPCORPUS_PATH.exists():
        print(textwrap.dedent(f"""
            Source C (MCPCorpus / arXiv:2506.23474): NOT FOUND — skipping.

            To use MCPCorpus as a drop-in replacement for the unavailable MCPCrawler data:
              1. Clone https://github.com/Snakinya/MCPCorpus
              2. Concatenate all JSON files under Crawler/Servers/ into one JSON array.
              3. Save to:  {MCPCORPUS_PATH}
              4. Re-run this script with --mcpcorpus.
        """).strip())
        return None

    with MCPCORPUS_PATH.open() as fh:
        raw = json.load(fh)

    # MCPCorpus JSON schema is TBC pending download; attempt best-effort mapping.
    # Common keys observed in similar corpora: "url"/"repo_url", "language", "stars"/"starCount"
    df = pd.DataFrame(raw)

    url_candidates = ["url", "repo_url", "github_url", "repository"]
    url_col = next((c for c in url_candidates if c in df.columns), None)
    if url_col is None:
        raise ValueError(
            f"MCPCorpus JSON has no recognisable URL key. Found keys: {list(df.columns)[:10]}"
        )

    df = df.rename(columns={url_col: "repo_url"})
    df["repo_url"] = df["repo_url"].map(_normalise_github_url)
    df["registry_source"] = "mcpcorpus-2506.23474"

    star_col = next((c for c in ["stars", "starCount", "star_count"] if c in df.columns), None)
    if star_col:
        df = df.rename(columns={star_col: "stars"})
    else:
        df["stars"] = pd.NA

    lang_col = next((c for c in ["language", "primaryLanguage", "lang"] if c in df.columns), None)
    if lang_col:
        df = df.rename(columns={lang_col: "language"})
    else:
        df["language"] = pd.NA

    df["category"] = pd.NA
    df["last_commit_date"] = pd.NaT

    canonical = ["repo_url", "registry_source", "category", "language", "stars", "last_commit_date"]
    df = df[[c for c in canonical if c in df.columns]]
    df["stars"] = pd.to_numeric(df["stars"], errors="coerce").astype("Int64")

    print(f"  Loaded {len(df):,} rows from Source C (MCPCorpus).")
    return df


# ────────────────────────────────────────────────────────────────────────────
# Merge + deduplication
# ────────────────────────────────────────────────────────────────────────────

def merge_and_deduplicate(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """
    Concatenate all source frames, drop rows with no valid repo_url, then
    deduplicate by repo_url.

    Deduplication strategy: keep the row with the highest star count (most
    informative). When stars are equal or both NaN, keep the first occurrence
    (i.e. prefer Source A, then B, then C — so the registry_source field
    reflects the primary source for each repo).
    """
    merged = pd.concat(frames, ignore_index=True)

    # Drop rows with no usable URL
    before = len(merged)
    merged = merged.dropna(subset=["repo_url"])
    merged = merged[merged["repo_url"].str.startswith("https://")]
    dropped = before - len(merged)
    if dropped:
        print(f"  Dropped {dropped:,} rows with missing or non-GitHub URLs.")

    # Sort so that when we keep the first duplicate, we keep the highest-star row.
    # NaN stars sort to the bottom with na_position='last'.
    merged = merged.sort_values("stars", ascending=False, na_position="last")
    dupes_before = merged.duplicated(subset="repo_url").sum()
    merged = merged.drop_duplicates(subset="repo_url", keep="first")
    if dupes_before:
        print(f"  Removed {dupes_before:,} duplicate repo URLs (kept highest-star entry).")

    # Restore a stable, registry-source-grouped order for readability
    merged = merged.sort_values(["registry_source", "repo_url"]).reset_index(drop=True)
    return merged


# ────────────────────────────────────────────────────────────────────────────
# Summary stats
# ────────────────────────────────────────────────────────────────────────────

def print_summary(df: pd.DataFrame) -> None:
    divider = "─" * 60
    print(f"\n{divider}")
    print(f"  SEED DATASET SUMMARY  →  {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(divider)
    print(f"  Total servers (deduplicated): {len(df):,}")

    print(f"\n  By registry source:")
    for src, count in df["registry_source"].value_counts().items():
        print(f"    {src:<40} {count:>5,}")

    print(f"\n  By primary language (top 10):")
    lang_counts = df["language"].fillna("(unknown)").value_counts()
    for lang, count in lang_counts.head(10).items():
        pct = 100 * count / len(df)
        print(f"    {lang:<30} {count:>5,}  ({pct:.1f}%)")
    if len(lang_counts) > 10:
        rest = lang_counts.iloc[10:].sum()
        print(f"    {'(other)':<30} {rest:>5,}  ({100*rest/len(df):.1f}%)")

    stars_known = df["stars"].dropna()
    if len(stars_known) > 0:
        print(f"\n  Star count (where available, n={len(stars_known):,}):")
        print(f"    median  {stars_known.median():.0f}")
        print(f"    mean    {stars_known.mean():.1f}")
        print(f"    max     {stars_known.max():.0f}")
        zero_star = (stars_known == 0).sum()
        print(f"    0-star  {zero_star:,}  ({100*zero_star/len(stars_known):.1f}%)")

    print(divider)


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--force-redownload",
        action="store_true",
        help="Re-download Source A even if a cached copy exists in data/raw/.",
    )
    p.add_argument(
        "--mcpcorpus",
        action="store_true",
        help="Include MCPCorpus (Source C) if data/raw/mcpcorpus_servers.json exists.",
    )
    p.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip Pydantic row-level validation (faster, but no schema checks).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []

    # ── Source A: SAIL Research (auto-download) ──────────────────────────────
    df_sail = load_sail_research(force_redownload=args.force_redownload)
    if not args.no_validate:
        df_sail = _validate_rows(df_sail, "Source A (SAIL Research)")
    frames.append(df_sail)

    # ── Source B: MCPCrawler (manual) ────────────────────────────────────────
    df_mcpc = load_mcpcrawler()
    if df_mcpc is not None:
        if not args.no_validate:
            df_mcpc = _validate_rows(df_mcpc, "Source B (MCPCrawler)")
        frames.append(df_mcpc)

    # ── Source C: MCPCorpus (optional) ───────────────────────────────────────
    if args.mcpcorpus:
        df_corpus = load_mcpcorpus()
        if df_corpus is not None:
            if not args.no_validate:
                df_corpus = _validate_rows(df_corpus, "Source C (MCPCorpus)")
            frames.append(df_corpus)

    if not frames:
        print("ERROR: No data loaded. Exiting.", file=sys.stderr)
        sys.exit(1)

    # ── Merge + dedup ─────────────────────────────────────────────────────────
    print("\nMerging sources …")
    merged = merge_and_deduplicate(frames)

    # ── Save ──────────────────────────────────────────────────────────────────
    merged.to_csv(OUTPUT_PATH, index=False)
    print(f"  Saved {len(merged):,} rows → {OUTPUT_PATH.relative_to(REPO_ROOT)}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print_summary(merged)


if __name__ == "__main__":
    main()

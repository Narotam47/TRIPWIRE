#!/usr/bin/env python3
"""
Script 07: Git history walker — tool definition drift reconstruction.

For each of the 292 successfully-extracted repos in batch_locate_results.jsonl,
walks the full git history of every file that contained tool definitions,
re-runs the validated locator at each commit, and records how each tool's
name / description / inputSchema changed over time.

Usage:
    python scripts/07_history_walk.py [--repos SLUG ...] [--limit N] [--out PATH]

    --repos  whitespace-separated owner__repo slugs to restrict to (test mode)
    --limit  max commits per file (default: unlimited)
    --out    output JSONL path (default: data/processed/tool_history.jsonl)
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tool_locator import (
    LocatedTool,
    extract_go,
    extract_python,
    extract_rust,
    extract_typescript,
    locate_tools,
)

# ── Paths ─────────────────────────────────────────────────────────────────
CLONE_DIR   = Path("data/raw/clones/full_batch")
RESULTS_IN  = Path("data/processed/batch_locate_results.jsonl")
DEFAULT_OUT = Path("data/processed/tool_history.jsonl")

# ── Per-extension extractor dispatch ──────────────────────────────────────
_EXT_MAP: dict[str, callable] = {
    ".ts":  extract_typescript,
    ".tsx": extract_typescript,
    ".js":  extract_typescript,
    ".mjs": extract_typescript,
    ".cjs": extract_typescript,
    ".py":  extract_python,
    ".go":  extract_go,
    ".rs":  extract_rust,
}


def _extractor_for(path: str):
    return _EXT_MAP.get(Path(path).suffix.lower())


# ── Git helpers ───────────────────────────────────────────────────────────

def repo_slug(repo_url: str) -> str:
    parts = repo_url.rstrip("/").split("/")
    return f"{parts[-2]}__{parts[-1]}"


def unshallow(repo_path: Path) -> bool:
    """Fetch full history if the clone is shallow. Returns True on success."""
    shallow_file = repo_path / ".git" / "shallow"
    if not shallow_file.exists():
        return True  # already full
    r = subprocess.run(
        ["git", "fetch", "--unshallow", "--quiet"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return r.returncode == 0


def commit_count(repo_path: Path) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_path, capture_output=True, text=True,
    )
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def file_history(repo_path: Path, rel_file: str) -> list[dict]:
    """
    Return chronological list of {sha, date, file_at_commit} for every
    commit that touched rel_file, following renames (--follow).
    Newest-first from git; we reverse to chronological order.
    """
    r = subprocess.run(
        [
            "git", "log",
            "--follow",          # track renames
            "--name-only",       # emit the filename at each commit
            "--format=COMMIT:%H %aI",  # %aI = author date ISO-8601 strict
            "--diff-filter=ACDMR",     # add/copy/delete/modify/rename only
            "--", rel_file,
        ],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return []

    commits: list[dict] = []
    pending = None
    for raw_line in r.stdout.split("\n"):
        line = raw_line.strip()
        if line.startswith("COMMIT:"):
            rest = line[7:]
            sha, _, date = rest.partition(" ")
            pending = {"sha": sha.strip(), "date": date.strip()}
        elif line and pending is not None:
            pending["file_at_commit"] = line
            commits.append(pending)
            pending = None

    return list(reversed(commits))  # chronological order


def content_at(repo_path: Path, sha: str, file_path: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f"{sha}:{file_path}"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else None


def detect_renames(commits: list[dict]) -> list[str]:
    """Return list of (old_path, new_path) rename transitions in the history."""
    paths = [c["file_at_commit"] for c in commits]
    renames = []
    for a, b in zip(paths, paths[1:]):
        if a != b:
            renames.append(f"{a} → {b}")
    return renames


def all_commits_chronological(repo_path: Path) -> list[dict]:
    """Return every commit in chronological (oldest-first) order."""
    r = subprocess.run(
        ["git", "log", "--format=%H %aI", "--reverse"],
        cwd=repo_path, capture_output=True, text=True,
    )
    commits = []
    for line in r.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        sha, _, date = line.partition(" ")
        commits.append({"sha": sha, "date": date})
    return commits


def archive_checkout(repo_path: Path, sha: str) -> Path | None:
    """Extract repo tree at sha into a temp dir. Returns Path or None on failure."""
    tmp = Path(tempfile.mkdtemp())
    arc = subprocess.run(
        ["git", "archive", "--format=tar", sha],
        cwd=repo_path, capture_output=True,
    )
    if arc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    tar = subprocess.run(
        ["tar", "-x", "-C", str(tmp)],
        input=arc.stdout, capture_output=True,
    )
    if tar.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    return tmp


def walk_repo_whole(
    repo_url: str,
    language: str,
    star_bucket: str,
    limit_commits: int | None = None,
    verbose: bool = False,
) -> tuple[list[dict], dict]:
    """
    Whole-repo-checkout history walk for repos using the cross-file
    python-ast-mcp-tool-call extractor.  At each commit we archive the full
    tree and run locate_tools() so multi-file resolution works correctly.
    """
    slug      = repo_slug(repo_url)
    repo_path = CLONE_DIR / slug
    stats     = dict(
        repo_url=repo_url, slug=slug, language=language,
        unshallow_ok=False, total_commits=0,
        files_walked=0, commits_walked=0,
        renames_found=[], errors=[],
        tools_at_head=0, total_records=0,
        walk_mode="whole-repo",
    )

    if not repo_path.exists():
        stats["errors"].append("clone_missing")
        return [], stats

    ok = unshallow(repo_path)
    stats["unshallow_ok"] = ok
    if not ok:
        stats["errors"].append("unshallow_failed")
        return [], stats

    commits = all_commits_chronological(repo_path)
    stats["total_commits"] = len(commits)

    if limit_commits:
        commits = commits[-limit_commits:]

    # files_walked = unique source files at HEAD (informational)
    head_tools = locate_tools(repo_path, language)
    stats["tools_at_head"] = len(head_tools)
    stats["files_walked"]  = len(set(t.source_file for t in head_tools))

    records: list[dict] = []

    for commit in commits:
        sha  = commit["sha"]
        date = commit["date"]

        tmp = archive_checkout(repo_path, sha)
        if tmp is None:
            stats["errors"].append(f"archive_failed:{sha[:8]}")
            continue

        try:
            hist_tools = locate_tools(tmp, language)
        except Exception as exc:
            stats["errors"].append(f"locate_error:{sha[:8]}:{exc}")
            continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        stats["commits_walked"] += 1

        seen: set[str] = set()
        for ht in hist_tools:
            if ht.tool_name in seen:
                continue
            seen.add(ht.tool_name)
            records.append(dict(
                repo_url     = repo_url,
                language     = language,
                star_bucket  = star_bucket,
                tool_name    = ht.tool_name,
                commit_sha   = sha,
                commit_date  = date,
                description  = ht.description,
                input_schema = ht.input_schema,
                source_file  = ht.source_file,
                extractor    = ht.extractor,
            ))

    stats["total_records"] = len(records)
    return records, stats


# ── Core walk ─────────────────────────────────────────────────────────────

def walk_repo(
    repo_url: str,
    language: str,
    star_bucket: str,
    limit_commits: int | None = None,
    verbose: bool = False,
) -> tuple[list[dict], dict]:
    """
    Walk full git history for one repo.

    Returns:
      records  — list of dicts (one per tool×commit) for JSONL
      stats    — summary dict for reporting
    """
    slug      = repo_slug(repo_url)
    repo_path = CLONE_DIR / slug
    stats     = dict(
        repo_url=repo_url, slug=slug, language=language,
        unshallow_ok=False, total_commits=0,
        files_walked=0, commits_walked=0,
        renames_found=[], errors=[],
        tools_at_head=0, total_records=0,
        walk_mode="per-file",
    )

    if not repo_path.exists():
        stats["errors"].append("clone_missing")
        return [], stats

    # 1. Unshallow
    ok = unshallow(repo_path)
    stats["unshallow_ok"] = ok
    if not ok:
        stats["errors"].append("unshallow_failed")
        return [], stats

    stats["total_commits"] = commit_count(repo_path)

    # 2. Locate current tools → get source files
    current_tools = locate_tools(repo_path, language)
    stats["tools_at_head"] = len(current_tools)
    if not current_tools:
        stats["errors"].append("no_tools_at_head")
        return [], stats

    # If the repo uses the cross-file python-ast-mcp-tool-call extractor, we
    # cannot reconstruct history from individual files — delegate to the
    # whole-repo-checkout walk instead.
    if any(t.extractor == "python-ast-mcp-tool-call" for t in current_tools):
        return walk_repo_whole(repo_url, language, star_bucket, limit_commits, verbose)

    # Group by source file
    files_to_tools: dict[str, list[LocatedTool]] = defaultdict(list)
    for t in current_tools:
        files_to_tools[t.source_file].append(t)

    records: list[dict] = []

    for rel_file, head_tools in files_to_tools.items():
        fn = _extractor_for(rel_file)
        if fn is None:
            continue  # .ipynb, .json, etc. — skip, no per-commit extractor

        commits = file_history(repo_path, rel_file)
        if not commits:
            continue

        # Track renames
        renames = detect_renames(commits)
        if renames:
            stats["renames_found"].extend([f"{rel_file}: {r}" for r in renames])

        if limit_commits:
            commits = commits[-limit_commits:]

        stats["files_walked"] += 1
        stats["commits_walked"] += len(commits)

        if verbose:
            print(f"    {rel_file}: {len(commits)} commits"
                  + (f"  renames: {renames}" if renames else ""))

        for commit in commits:
            sha      = commit["sha"]
            date     = commit["date"]
            file_at  = commit["file_at_commit"]

            src = content_at(repo_path, sha, file_at)
            if src is None:
                continue

            try:
                hist_tools = fn(src, file_at)
            except Exception as exc:
                stats["errors"].append(f"{sha[:8]}:{file_at}: {exc}")
                continue

            seen = set()
            for ht in hist_tools:
                if ht.tool_name in seen:
                    continue
                seen.add(ht.tool_name)
                records.append(dict(
                    repo_url     = repo_url,
                    language     = language,
                    star_bucket  = star_bucket,
                    tool_name    = ht.tool_name,
                    commit_sha   = sha,
                    commit_date  = date,
                    description  = ht.description,
                    input_schema = ht.input_schema,
                    source_file  = file_at,
                    extractor    = ht.extractor,
                ))

    stats["total_records"] = len(records)
    return records, stats


# ── CLI ───────────────────────────────────────────────────────────────────

def load_target_repos(
    only_slugs: set[str] | None = None,
    force_include: bool = False,
) -> list[dict]:
    """
    Load extraction-successful primary repos from the checkpoint.

    force_include=True: when only_slugs is given, load matching records
    regardless of replacement_needed / tools_found (for verifying that
    previously-fixed repos are correctly skipped by the walker).
    """
    recs = [json.loads(l) for l in RESULTS_IN.open()]
    targets = []
    for r in recs:
        if "is_replacement_for" in r:
            continue
        slug = repo_slug(r["repo_url"])
        if only_slugs and slug not in only_slugs:
            continue
        # In force_include mode with explicit slug list, bypass normal filters
        # so we can verify repos with 0 tools are correctly handled.
        if not force_include:
            if r.get("replacement_needed"):
                continue
            if r.get("tools_found", 0) == 0:
                continue
        targets.append(dict(
            repo_url    = r["repo_url"],
            language    = r.get("language", ""),
            star_bucket = r.get("star_bucket", ""),
            slug        = slug,
        ))
    return targets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos",  nargs="+", default=None,
                    help="owner__repo slugs to process (test mode)")
    ap.add_argument("--limit",  type=int,  default=None,
                    help="max commits per file")
    ap.add_argument("--out",    default=str(DEFAULT_OUT),
                    help="output JSONL path")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--force-include", action="store_true",
                    help="load all slugs in --repos regardless of replacement_needed "
                         "(use with --repos to verify zero-tool repos are skipped)")
    args = ap.parse_args()

    only_slugs    = set(args.repos) if args.repos else None
    force_include = args.force_include and bool(only_slugs)
    targets       = load_target_repos(only_slugs, force_include=force_include)

    if not targets:
        print("No matching repos found.", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Append mode: skip repos already written
    done_repos: set[str] = set()
    if out_path.exists():
        for line in out_path.open():
            try:
                done_repos.add(json.loads(line)["repo_url"])
            except Exception:
                pass

    print(f"History walk — {len(targets)} repos"
          + (f" (skipping {len(done_repos)} already done)" if done_repos else ""))

    all_stats: list[dict] = []
    t0 = time.time()

    with out_path.open("a") as fout:
        for i, repo in enumerate(targets, 1):
            if repo["repo_url"] in done_repos:
                continue
            print(f"  {i}/{len(targets)}  {repo['slug']} "
                  f"[{repo['language']}] ...", end=" ", flush=True)
            t1 = time.time()

            records, stats = walk_repo(
                repo["repo_url"],
                repo["language"],
                repo["star_bucket"],
                limit_commits=args.limit,
                verbose=args.verbose,
            )

            elapsed = time.time() - t1
            print(f"{stats['total_commits']} commits  "
                  f"{stats['files_walked']} files  "
                  f"{stats['commits_walked']} file-commits  "
                  f"{len(records)} records  "
                  f"({elapsed:.1f}s)"
                  + (f"  RENAMES: {stats['renames_found']}" if stats["renames_found"] else "")
                  + (f"  ERRORS: {stats['errors']}" if stats["errors"] else ""))

            for rec in records:
                fout.write(json.dumps(rec) + "\n")
            all_stats.append(stats)

    print(f"\n{'─'*72}")
    print(f"Total time: {time.time()-t0:.1f}s")
    total_rec = sum(s["total_records"] for s in all_stats)
    total_repo_ok = sum(1 for s in all_stats if not s["errors"] and s["total_records"] > 0)
    print(f"Repos with records: {total_repo_ok}/{len(all_stats)}")
    print(f"Total records written: {total_rec}")
    renames_seen = [r for s in all_stats for r in s["renames_found"]]
    if renames_seen:
        print(f"File renames detected ({len(renames_seen)}):")
        for r in renames_seen:
            print(f"  {r}")


if __name__ == "__main__":
    main()

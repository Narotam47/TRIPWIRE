"""
Repo cloner for the MCP drift study pipeline.

Uses subprocess git directly (more predictable than GitPython for clone ops).
Full clones (no --depth) are the default so git-history mining works;
pass depth=1 for fast test-batch validation runs.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CloneResult:
    repo_url: str
    success: bool
    clone_path: Path | None = None
    error: str | None = None
    commit_sha: str | None = None
    commit_date: datetime | None = None
    file_count: int = 0


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )
    return result.returncode, (result.stderr or result.stdout).strip()


def _repo_slug(repo_url: str) -> str:
    """Turn 'https://github.com/owner/repo' into 'owner__repo'."""
    parts = repo_url.rstrip("/").split("/")
    return "__".join(parts[-2:]) if len(parts) >= 2 else parts[-1]


def clone(repo_url: str, clone_root: Path, depth: int | None = None) -> CloneResult:
    """
    Clone *repo_url* into *clone_root/<owner>__<repo>/*.

    Parameters
    ----------
    depth : int | None
        If set, passes --depth=N for a shallow clone.  Pass 1 for test-batch
        runs.  Leave None for full-history mining clones.
    """
    dest = clone_root / _repo_slug(repo_url)

    if dest.exists() and (dest / ".git").exists():
        # Already cloned — pull HEAD to ensure it's current
        rc, err = _run(["git", "fetch", "--quiet"], cwd=dest)
        if rc != 0:
            # fetch failed (network, auth) — use what we have
            pass
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--quiet"]
        if depth is not None:
            cmd += [f"--depth={depth}"]
        cmd += [repo_url, str(dest)]
        try:
            rc, err = _run(cmd, timeout=120)
        except subprocess.TimeoutExpired:
            return CloneResult(repo_url=repo_url, success=False, error="clone timed out after 120s")
        if rc != 0:
            return CloneResult(repo_url=repo_url, success=False, error=err or "git clone returned non-zero")

    # Read HEAD commit metadata
    sha, sha_err     = _run(["git", "log", "-1", "--format=%H"],  cwd=dest)
    date_str, _      = _run(["git", "log", "-1", "--format=%aI"], cwd=dest)
    commit_sha       = sha.strip()   if sha_err   == 0 else None  # type: ignore[arg-type]
    # rc check: sha_err is a string here (we unpacked rc, err above)
    # re-do correctly
    rc_sha,  sha_out  = _run(["git", "log", "-1", "--format=%H"],  cwd=dest)
    rc_date, date_out = _run(["git", "log", "-1", "--format=%aI"], cwd=dest)

    commit_sha = sha_out.strip() if rc_sha == 0 else None
    commit_date: datetime | None = None
    if rc_date == 0 and date_out.strip():
        try:
            commit_date = datetime.fromisoformat(date_out.strip())
            if commit_date.tzinfo is None:
                commit_date = commit_date.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    file_count = sum(1 for _ in dest.rglob("*") if _.is_file() and ".git" not in _.parts)

    return CloneResult(
        repo_url=repo_url,
        success=True,
        clone_path=dest,
        commit_sha=commit_sha,
        commit_date=commit_date,
        file_count=file_count,
    )

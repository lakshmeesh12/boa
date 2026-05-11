"""Git-based change detection.

Wraps `git` subprocess calls to compute the ChangeSet between the baseline tag
and the current HEAD. The watcher is read-only — it never mutates the repo.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from core.logging_config import get_logger
from models.schemas import ChangedFile, ChangeSet

log = get_logger("GitWatcher")

# Walk up to repo root (same logic as core/settings.py)
_REPO_ROOT = Path(__file__).resolve().parents[3]

_LANG_BY_EXT = {
    ".py":   "python",
    ".js":   "javascript",
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".html": "html",
    ".css":  "css",
    ".json": "json",
    ".yml":  "yaml",
    ".yaml": "yaml",
    ".sh":   "bash",
    ".ps1":  "powershell",
    ".conf": "config",
    ".md":   "markdown",
    ".txt":  "text",
    ".toml": "toml",
}

# Max bytes of diff text per file before truncation (keeps Claude prompts small)
_MAX_DIFF_PER_FILE = 8000


class GitWatcher:
    def __init__(self, baseline_tag: str = "aqe-demo-baseline", repo_root: Path | None = None):
        self.baseline_tag = baseline_tag
        self.repo_root = repo_root or _REPO_ROOT

    async def _run_git(self, *args: str) -> tuple[int, str, str]:
        """Run `git <args>` in the repo root; return (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(self.repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await proc.communicate()
        return proc.returncode or 0, out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace")

    async def is_repo(self) -> bool:
        rc, _, _ = await self._run_git("rev-parse", "--is-inside-work-tree")
        return rc == 0

    async def baseline_exists(self) -> bool:
        rc, _, _ = await self._run_git("rev-parse", "--verify", f"refs/tags/{self.baseline_tag}")
        return rc == 0

    async def get_sha(self, ref: str) -> str:
        rc, out, err = await self._run_git("rev-parse", ref)
        if rc != 0:
            raise RuntimeError(f"git rev-parse {ref} failed: {err.strip()}")
        return out.strip()

    async def get_branch(self) -> str:
        rc, out, _ = await self._run_git("symbolic-ref", "--short", "HEAD")
        return out.strip() if rc == 0 else "DETACHED"

    async def _changed_paths(self, base_sha: str, head_sha: str) -> list[tuple[str, str]]:
        """Returns [(status_letter, path), ...] using `git diff --name-status`."""
        rc, out, err = await self._run_git(
            "diff", "--name-status", f"{base_sha}..{head_sha}",
        )
        if rc != 0:
            raise RuntimeError(f"git diff --name-status failed: {err.strip()}")
        results: list[tuple[str, str]] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                # Renames look like "R100\told\tnew" — we treat the new path as the entry.
                status = parts[0][0]
                path = parts[-1]
                results.append((status, path))
        return results

    async def _file_diff(self, base_sha: str, head_sha: str, path: str) -> tuple[int, int, str]:
        """Returns (additions, deletions, unified_diff_text) for a single file."""
        # numstat
        rc, out, _ = await self._run_git("diff", "--numstat", f"{base_sha}..{head_sha}", "--", path)
        adds = dels = 0
        if rc == 0 and out.strip():
            try:
                a, d, _ = out.strip().split("\t", 2)
                adds = int(a) if a.isdigit() else 0
                dels = int(d) if d.isdigit() else 0
            except ValueError:
                pass
        # unified diff (capped)
        rc2, diff_text, _ = await self._run_git(
            "diff", "--unified=3", f"{base_sha}..{head_sha}", "--", path,
        )
        if rc2 != 0:
            diff_text = ""
        if len(diff_text) > _MAX_DIFF_PER_FILE:
            diff_text = diff_text[:_MAX_DIFF_PER_FILE] + "\n... (truncated)\n"
        return adds, dels, diff_text

    @staticmethod
    def _status_word(letter: str) -> str:
        return {
            "A": "added",
            "M": "modified",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "T": "type_changed",
        }.get(letter, "modified")

    @staticmethod
    def _language(path: str) -> str:
        ext = Path(path).suffix.lower()
        return _LANG_BY_EXT.get(ext, "unknown")

    async def compute_changeset(self) -> ChangeSet:
        """Build a full ChangeSet comparing baseline..HEAD.

        Returns an empty ChangeSet if HEAD == baseline.
        Raises RuntimeError if the repo or baseline tag is missing.
        """
        if not await self.is_repo():
            raise RuntimeError(f"{self.repo_root} is not a git repo")
        if not await self.baseline_exists():
            raise RuntimeError(
                f"baseline tag '{self.baseline_tag}' not found. "
                "Run demo_scripts/setup_demo_repo.ps1 first."
            )

        base_sha = await self.get_sha(self.baseline_tag)
        head_sha = await self.get_sha("HEAD")
        branch = await self.get_branch()

        if base_sha == head_sha:
            log.info("git_watcher.no_changes", context={"sha": head_sha})
            return ChangeSet(baseline_sha=base_sha, head_sha=head_sha, branch=branch, files=[])

        paths = await self._changed_paths(base_sha, head_sha)
        files: list[ChangedFile] = []
        total_adds = total_dels = 0

        for status_letter, path in paths:
            adds, dels, diff_text = await self._file_diff(base_sha, head_sha, path)
            files.append(ChangedFile(
                path=path,
                status=self._status_word(status_letter),
                language=self._language(path),
                additions=adds,
                deletions=dels,
                diff=diff_text,
            ))
            total_adds += adds
            total_dels += dels

        cs = ChangeSet(
            baseline_sha=base_sha,
            head_sha=head_sha,
            branch=branch,
            files=files,
            total_additions=total_adds,
            total_deletions=total_dels,
        )
        log.info(
            "git_watcher.changeset_computed",
            context={"files": len(files), "additions": total_adds, "deletions": total_dels},
        )
        return cs

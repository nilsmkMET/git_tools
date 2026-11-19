#!/usr/bin/env python3
"""Inspect git status across all first-level directories and provide an actionable summary.

Scans the immediate subdirectories of a given target folder, checks their git status,
tracks whether they are ahead, behind, diverged, dirty (uncommitted changes), or have
no upstream tracking branch, and prints a comprehensive overview and categorized
action summary.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Top-level configurable constants
DEFAULT_FETCH_TIMEOUT_SECONDS: int = 8
DEFAULT_GIT_COMMAND: str = "git"
DEFAULT_TARGET_DIRECTORY: str = "."
DEFAULT_INCLUDE_HIDDEN: bool = False
DEFAULT_EXCLUDED_DIR_NAMES: tuple[str, ...] = ("__pycache__", ".pytest_cache", ".git")
ENV_DISABLE_PROMPT: str = "GIT_TERMINAL_PROMPT"


@dataclass
class RepoStatus:
    """Represents the git status of an inspected directory.

    Attributes:
        name: Name of the repository directory.
        path: Absolute Path to the repository.
        is_git: Whether this directory is a valid git repository.
        branch: Current active branch name or '(detached)'.
        upstream: Upstream tracking branch if configured.
        ahead: Number of commits ahead of upstream.
        behind: Number of commits behind upstream.
        staged: Number of files with staged changes.
        unstaged: Number of tracked files with unstaged modifications or deletions.
        untracked: Number of untracked files.
        unmerged: Number of unmerged / conflicted files.
        fetch_attempted: Whether a fetch was attempted.
        fetch_failed: Whether a fetch attempt failed or timed out.
        error_message: Any fatal error encountered during inspection.
    """

    name: str
    path: Path
    is_git: bool
    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    unmerged: int = 0
    fetch_attempted: bool = False
    fetch_failed: bool = False
    error_message: str | None = None

    @property
    def has_uncommitted(self) -> bool:
        """Check if working directory has any uncommitted changes."""
        return (self.staged + self.unstaged + self.untracked + self.unmerged) > 0

    @property
    def needs_push(self) -> bool:
        """Check if local branch has commits to push."""
        return self.ahead > 0 and self.behind == 0

    @property
    def needs_pull(self) -> bool:
        """Check if remote has commits to pull."""
        return self.behind > 0 and self.ahead == 0

    @property
    def is_diverged(self) -> bool:
        """Check if branch has both unpushed and unpulled commits."""
        return self.ahead > 0 and self.behind > 0

    @property
    def has_no_upstream(self) -> bool:
        """Check if branch exists but has no tracking upstream branch."""
        return self.is_git and self.branch is not None and self.upstream is None

    @property
    def is_clean_and_synced(self) -> bool:
        """Check if repository is completely clean and up to date with upstream."""
        return (
            self.is_git
            and not self.has_uncommitted
            and self.upstream is not None
            and self.ahead == 0
            and self.behind == 0
            and self.error_message is None
        )

    @property
    def needs_attention(self) -> bool:
        """Check if this repository requires any developer action."""
        if not self.is_git or self.error_message is not None:
            return True
        return not self.is_clean_and_synced


def parse_branch_header_line(line: str, status: RepoStatus) -> None:
    """Parse git status porcelain v2 branch metadata line.

    Args:
        line: Raw line starting with '# branch.'.
        status: RepoStatus instance to update in-place.
    """
    parts = line.split()
    if len(parts) < 3:
        return

    key = parts[1]
    if key == "branch.head":
        status.branch = parts[2]
    elif key == "branch.upstream":
        status.upstream = parts[2]
    elif key == "branch.ab" and len(parts) >= 4:
        raw_ahead = parts[2]
        raw_behind = parts[3]
        if raw_ahead.startswith("+"):
            status.ahead = int(raw_ahead[1:])
        if raw_behind.startswith("-"):
            status.behind = int(raw_behind[1:])


def _parse_tracked_file_status(xy: str, status: RepoStatus) -> None:
    """Parse index and worktree status flags for a tracked file."""
    if len(xy) >= 2:
        if xy[0] != ".":
            status.staged += 1
        if xy[1] != ".":
            status.unstaged += 1


def parse_porcelain_entry_line(line: str, status: RepoStatus) -> None:
    """Parse a single porcelain v2 file change entry line.

    Args:
        line: Raw status line.
        status: RepoStatus instance to update in-place.
    """
    if not line:
        return

    entry_type = line[0]
    if entry_type == "?":
        status.untracked += 1
    elif entry_type == "u":
        status.unmerged += 1
    elif entry_type in ("1", "2"):
        tokens = line.split(maxsplit=2)
        if len(tokens) >= 2:
            _parse_tracked_file_status(tokens[1], status)


def parse_status_porcelain_v2(output: str, status: RepoStatus) -> RepoStatus:
    """Parse the full porcelain v2 status command output.

    Args:
        output: Raw stdout from `git status --porcelain=v2 --branch`.
        status: RepoStatus instance to populate.

    Returns:
        Updated RepoStatus instance.
    """
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# branch."):
            parse_branch_header_line(line, status)
        else:
            parse_porcelain_entry_line(line, status)
    return status


def is_git_repository(repo_path: Path, git_cmd: str = DEFAULT_GIT_COMMAND) -> bool:
    """Determine whether a given path is a valid git work tree.

    Args:
        repo_path: Target directory path.
        git_cmd: Git executable name or path.

    Returns:
        True if the directory is a git repository, False otherwise.
    """
    cmd = [git_cmd, "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except (FileNotFoundError, PermissionError):
        return False


def run_git_fetch(
    repo_path: Path,
    git_cmd: str = DEFAULT_GIT_COMMAND,
    timeout: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> bool:
    """Run non-interactive git fetch on a repository.

    Args:
        repo_path: Path to the git repository.
        git_cmd: Git executable name or path.
        timeout: Fetch timeout in seconds.

    Returns:
        True if fetch succeeded, False otherwise.
    """
    env = os.environ.copy()
    env[ENV_DISABLE_PROMPT] = "0"

    cmd = [git_cmd, "-C", str(repo_path), "fetch", "--prune", "--quiet"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return False


def inspect_repository(
    repo_path: Path,
    fetch: bool = False,
    fetch_timeout: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
    git_cmd: str = DEFAULT_GIT_COMMAND,
) -> RepoStatus:
    """Inspect the git state of a single directory.

    Args:
        repo_path: Directory path to inspect.
        fetch: Whether to fetch remotes prior to checking status.
        fetch_timeout: Timeout in seconds for git fetch.
        git_cmd: Git executable name or path.

    Returns:
        Populated RepoStatus instance.
    """
    name = repo_path.name
    status = RepoStatus(name=name, path=repo_path.resolve(), is_git=False)

    if not repo_path.is_dir():
        status.error_message = "Not a directory"
        return status

    if not is_git_repository(repo_path, git_cmd=git_cmd):
        status.error_message = "Not a git repository"
        return status

    status.is_git = True

    if fetch:
        status.fetch_attempted = True
        fetch_ok = run_git_fetch(repo_path, git_cmd=git_cmd, timeout=fetch_timeout)
        if not fetch_ok:
            status.fetch_failed = True

    cmd = [git_cmd, "-C", str(repo_path), "status", "--porcelain=v2", "--branch"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            status.error_message = (
                proc.stderr.strip() or "git status exited with non-zero status"
            )
            return status
        parse_status_porcelain_v2(proc.stdout, status)
    except (FileNotFoundError, PermissionError) as err:
        status.error_message = str(err)

    return status


def scan_first_level_directories(
    root_dir: Path,
    fetch: bool = False,
    fetch_timeout: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
    git_cmd: str = DEFAULT_GIT_COMMAND,
    include_hidden: bool = DEFAULT_INCLUDE_HIDDEN,
    excluded_names: tuple[str, ...] = DEFAULT_EXCLUDED_DIR_NAMES,
    git_only: bool = False,
) -> list[RepoStatus]:
    """Scan all immediate subdirectories under root_dir and check git status.

    Args:
        root_dir: Base directory whose immediate child folders are inspected.
        fetch: Whether to fetch remotes prior to checking status.
        fetch_timeout: Timeout in seconds for git fetch.
        git_cmd: Git executable name or path.
        include_hidden: Whether to inspect hidden directories (starting with '.').
        excluded_names: Directory names to ignore automatically.
        git_only: If True, omit directories that are not git repositories.

    Returns:
        List of RepoStatus objects sorted alphabetically by directory name.
    """
    assert root_dir.exists(), f"Target root directory '{root_dir}' does not exist"
    assert root_dir.is_dir(), f"Target root path '{root_dir}' is not a directory"

    subdirs = [
        p for p in root_dir.iterdir() if p.is_dir() and p.name not in excluded_names
    ]
    if not include_hidden:
        subdirs = [p for p in subdirs if not p.name.startswith(".")]

    subdirs.sort(key=lambda p: p.name.lower())

    results: list[RepoStatus] = []
    for subdir in subdirs:
        repo_status = inspect_repository(
            repo_path=subdir,
            fetch=fetch,
            fetch_timeout=fetch_timeout,
            git_cmd=git_cmd,
        )
        if git_only and not repo_status.is_git:
            continue
        results.append(repo_status)

    return results


def _colorize(text: str, color_code: str, use_color: bool) -> str:
    """Format text with ANSI color codes when enabled."""
    if not use_color:
        return text
    return f"\033[{color_code}m{text}\033[0m"


def build_status_badge(status: RepoStatus, use_color: bool = False) -> str:
    """Build a concise visual status badge for a repository.

    Args:
        status: RepoStatus instance.
        use_color: Whether to include ANSI color escape codes.

    Returns:
        Concise status badge string.
    """
    if not status.is_git:
        return _colorize("NOT A GIT REPO", "90", use_color)
    if status.error_message:
        return _colorize("ERROR", "31;1", use_color)

    tags: list[str] = []
    if status.has_uncommitted:
        tags.append(_colorize("DIRTY", "31;1", use_color))
    if status.is_diverged:
        tags.append(
            _colorize(f"DIVERGED(+{status.ahead}/-{status.behind})", "35;1", use_color)
        )
    elif status.needs_push:
        tags.append(_colorize(f"AHEAD(+{status.ahead})", "33;1", use_color))
    elif status.needs_pull:
        tags.append(_colorize(f"BEHIND(-{status.behind})", "34;1", use_color))

    if status.has_no_upstream:
        tags.append(_colorize("NO UPSTREAM", "36;1", use_color))

    if not tags:
        return _colorize("CLEAN & SYNCED", "32;1", use_color)

    return " | ".join(tags)


def _format_row_details(s: RepoStatus) -> str:
    """Format single row details column."""
    detail_parts: list[str] = []
    if s.staged > 0:
        detail_parts.append(f"staged:{s.staged}")
    if s.unstaged > 0:
        detail_parts.append(f"modified:{s.unstaged}")
    if s.untracked > 0:
        detail_parts.append(f"untracked:{s.untracked}")
    if s.unmerged > 0:
        detail_parts.append(f"conflicts:{s.unmerged}")
    if s.fetch_failed:
        detail_parts.append("fetch:failed")
    if s.error_message:
        detail_parts.append(f"msg:{s.error_message}")
    return ", ".join(detail_parts) if detail_parts else "working tree clean"


def format_overview_table(statuses: list[RepoStatus], use_color: bool = False) -> str:
    """Generate a clean tabular overview of all inspected directories.

    Args:
        statuses: List of inspected repo statuses.
        use_color: Whether to use ANSI terminal colors.

    Returns:
        Formatted multi-line table string.
    """
    if not statuses:
        return "No subdirectories found to inspect."

    lines: list[str] = []
    header_repo = "REPOSITORY"
    header_branch = "BRANCH"
    header_upstream = "UPSTREAM"
    header_status = "STATUS"
    header_details = "CHANGES"

    max_repo = max(len(header_repo), max(len(s.name) for s in statuses))
    max_branch = max(len(header_branch), max(len(s.branch or "-") for s in statuses))
    max_upstream = max(
        len(header_upstream), max(len(s.upstream or "-") for s in statuses)
    )

    row_fmt = (
        f"{{:<{max_repo}}}  {{:<{max_branch}}}  {{:<{max_upstream}}}  {{:<30}}  {{}}"
    )
    divider = "-" * (max_repo + max_branch + max_upstream + 60)

    lines.append(divider)
    lines.append(
        row_fmt.format(
            header_repo, header_branch, header_upstream, header_status, header_details
        )
    )
    lines.append(divider)

    for s in statuses:
        branch_str = s.branch or "-"
        upstream_str = s.upstream or "-"
        badge = build_status_badge(s, use_color=use_color)
        details_str = _format_row_details(s)
        lines.append(
            row_fmt.format(s.name, branch_str, upstream_str, badge, details_str)
        )

    lines.append(divider)
    return "\n".join(lines)


def _format_pull_section(need_pull: list[RepoStatus], use_color: bool) -> list[str]:
    """Format section for repositories needing pull."""
    if not need_pull:
        return []
    lines = [
        "",
        _colorize(
            f"📥 REPOSITORIES BEHIND UPSTREAM (Need `git pull`): {len(need_pull)}",
            "34;1",
            use_color,
        ),
    ]
    for s in need_pull:
        lines.append(
            f"  • {s.name} ({s.branch} is behind {s.upstream} by {s.behind} commit(s))"
        )
        lines.append(f"    --> Command: git -C {s.name} pull")
    return lines


def _format_push_section(need_push: list[RepoStatus], use_color: bool) -> list[str]:
    """Format section for repositories needing push."""
    if not need_push:
        return []
    lines = [
        "",
        _colorize(
            f"📤 REPOSITORIES AHEAD OF UPSTREAM (Need `git push`): {len(need_push)}",
            "33;1",
            use_color,
        ),
    ]
    for s in need_push:
        lines.append(
            f"  • {s.name} ({s.branch} is ahead of {s.upstream} by {s.ahead} commit(s))"
        )
        lines.append(f"    --> Command: git -C {s.name} push")
    return lines


def _format_diverged_section(diverged: list[RepoStatus], use_color: bool) -> list[str]:
    """Format section for diverged repositories."""
    if not diverged:
        return []
    lines = [
        "",
        _colorize(
            f"⚠️  DIVERGED REPOSITORIES (Need rebase or merge): {len(diverged)}",
            "35;1",
            use_color,
        ),
    ]
    for s in diverged:
        lines.append(
            f"  • {s.name} (ahead +{s.ahead}, behind -{s.behind} vs {s.upstream})"
        )
        lines.append(f"    --> Command: git -C {s.name} pull --rebase")
    return lines


def _format_dirty_section(dirty: list[RepoStatus], use_color: bool) -> list[str]:
    """Format section for repositories with uncommitted modifications."""
    if not dirty:
        return []
    lines = [
        "",
        _colorize(
            f"📝 UNCOMMITTED CHANGES (Need commit / stash): {len(dirty)}",
            "31;1",
            use_color,
        ),
    ]
    for s in dirty:
        items: list[str] = []
        if s.staged:
            items.append(f"{s.staged} staged")
        if s.unstaged:
            items.append(f"{s.unstaged} modified/deleted")
        if s.untracked:
            items.append(f"{s.untracked} untracked")
        if s.unmerged:
            items.append(f"{s.unmerged} conflicts")
        lines.append(f"  • {s.name}: {', '.join(items)}")
        lines.append(f"    --> Command: git -C {s.name} status")
    return lines


def _format_no_upstream_section(
    no_upstream: list[RepoStatus], use_color: bool
) -> list[str]:
    """Format section for repositories lacking an upstream tracking branch."""
    if not no_upstream:
        return []
    lines = [
        "",
        _colorize(
            f"🔗 NO UPSTREAM TRACKING BRANCH: {len(no_upstream)}", "36;1", use_color
        ),
    ]
    for s in no_upstream:
        lines.append(f"  • {s.name} (branch '{s.branch}' is not tracking any remote)")
        lines.append(f"    --> Command: git -C {s.name} push -u origin {s.branch}")
    return lines


def _format_clean_section(clean_synced: list[RepoStatus], use_color: bool) -> list[str]:
    """Format section for cleanly synchronized repositories."""
    if not clean_synced:
        return []
    lines = [
        "",
        _colorize(f"✅ UP TO DATE & CLEAN: {len(clean_synced)}", "32;1", use_color),
    ]
    for s in clean_synced:
        lines.append(f"  • {s.name} ({s.branch} == {s.upstream})")
    return lines


def _format_non_git_section(
    non_git_dirs: list[RepoStatus], use_color: bool
) -> list[str]:
    """Format section for directories that are not git repositories."""
    if not non_git_dirs:
        return []
    lines = [
        "",
        _colorize(f"📁 NON-GIT DIRECTORIES: {len(non_git_dirs)}", "90", use_color),
    ]
    for s in non_git_dirs:
        lines.append(f"  • {s.name}")
    return lines


def _format_footer_statistics(statuses: list[RepoStatus], use_color: bool) -> list[str]:
    """Format summary footer statistics."""
    git_repos = [s for s in statuses if s.is_git]
    clean_synced = [s for s in git_repos if s.is_clean_and_synced]
    needing_action = [s for s in git_repos if s.needs_attention]

    lines = [
        "",
        _colorize("--------------------------------------------------", "1", use_color),
        _colorize(
            f"Total Inspected: {len(statuses)} | "
            f"Git Repos: {len(git_repos)} | "
            f"Clean & Synced: {len(clean_synced)} | "
            f"Needing Action: {len(needing_action)}",
            "1",
            use_color,
        ),
        _colorize("--------------------------------------------------", "1", use_color),
    ]
    return lines


@dataclass
class GroupedRepos:
    """Categorized repository collections."""

    git_repos: list[RepoStatus]
    non_git: list[RepoStatus]
    need_pull: list[RepoStatus]
    need_push: list[RepoStatus]
    diverged: list[RepoStatus]
    dirty: list[RepoStatus]
    no_upstream: list[RepoStatus]
    clean_synced: list[RepoStatus]


def _group_repositories(statuses: list[RepoStatus]) -> GroupedRepos:
    """Group repositories into action categories."""
    groups = GroupedRepos(
        git_repos=[],
        non_git=[],
        need_pull=[],
        need_push=[],
        diverged=[],
        dirty=[],
        no_upstream=[],
        clean_synced=[],
    )
    for s in statuses:
        if not s.is_git:
            groups.non_git.append(s)
            continue
        groups.git_repos.append(s)
        if s.needs_pull:
            groups.need_pull.append(s)
        if s.needs_push:
            groups.need_push.append(s)
        if s.is_diverged:
            groups.diverged.append(s)
        if s.has_uncommitted:
            groups.dirty.append(s)
        if s.has_no_upstream:
            groups.no_upstream.append(s)
        if s.is_clean_and_synced:
            groups.clean_synced.append(s)
    return groups


def format_action_summary(statuses: list[RepoStatus], use_color: bool = False) -> str:
    """Format categorized action lists summarizing required developer updates.

    Args:
        statuses: List of inspected repo statuses.
        use_color: Whether to use ANSI terminal colors.

    Returns:
        Formatted multi-line summary of required actions.
    """
    lines: list[str] = [
        _colorize("==================================================", "1", use_color),
        _colorize(
            "             GIT STATUS ACTION SUMMARY            ", "1;7", use_color
        ),
        _colorize("==================================================", "1", use_color),
    ]

    groups = _group_repositories(statuses)

    lines.extend(_format_pull_section(groups.need_pull, use_color))
    lines.extend(_format_push_section(groups.need_push, use_color))
    lines.extend(_format_diverged_section(groups.diverged, use_color))
    lines.extend(_format_dirty_section(groups.dirty, use_color))
    lines.extend(_format_no_upstream_section(groups.no_upstream, use_color))
    lines.extend(_format_clean_section(groups.clean_synced, use_color))
    lines.extend(_format_non_git_section(groups.non_git, use_color))
    lines.extend(_format_footer_statistics(statuses, use_color))

    return "\n".join(lines)


def generate_json_summary(statuses: list[RepoStatus]) -> dict[str, Any]:
    """Generate machine-readable JSON dictionary representing scan results.

    Args:
        statuses: Inspected repo status list.

    Returns:
        JSON-serializable dictionary.
    """
    serialized_repos = []
    for s in statuses:
        data = asdict(s)
        data["path"] = str(s.path)
        data["has_uncommitted"] = s.has_uncommitted
        data["needs_push"] = s.needs_push
        data["needs_pull"] = s.needs_pull
        data["is_diverged"] = s.is_diverged
        data["has_no_upstream"] = s.has_no_upstream
        data["is_clean_and_synced"] = s.is_clean_and_synced
        data["needs_attention"] = s.needs_attention
        serialized_repos.append(data)

    return {
        "total_inspected": len(statuses),
        "total_git_repos": sum(1 for s in statuses if s.is_git),
        "clean_and_synced": sum(1 for s in statuses if s.is_clean_and_synced),
        "needing_action": sum(1 for s in statuses if s.is_git and s.needs_attention),
        "repositories": serialized_repos,
    }


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments.

    Args:
        argv: List of argument strings, or None to use sys.argv[1:].

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Propagate into first-level directories and inspect git status with an action summary.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_TARGET_DIRECTORY,
        help="Target base directory containing folders to inspect.",
    )
    parser.add_argument(
        "-f",
        "--fetch",
        action="store_true",
        help="Fetch latest remote tracking branches prior to checking status (uses git fetch).",
    )
    parser.add_argument(
        "--fetch-timeout",
        type=int,
        default=DEFAULT_FETCH_TIMEOUT_SECONDS,
        help="Timeout in seconds for fetching each repository.",
    )
    parser.add_argument(
        "-g",
        "--git-only",
        action="store_true",
        help="Only display and summarize valid git repositories (omit non-git folders).",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        default=DEFAULT_INCLUDE_HIDDEN,
        help="Include hidden folders (names starting with dot).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI terminal colors.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the git status propagation script.

    Args:
        argv: Optional command line argument list.

    Returns:
        0 on successful scan, 1 on fatal error.
    """
    args = parse_cli_args(argv)

    if shutil.which(DEFAULT_GIT_COMMAND) is None:
        sys.stderr.write(
            f"Error: Git executable '{DEFAULT_GIT_COMMAND}' not found in PATH.\n"
        )
        return 1

    target_path = Path(args.target).resolve()
    if not target_path.exists() or not target_path.is_dir():
        sys.stderr.write(
            f"Error: Target path '{args.target}' does not exist or is not a directory.\n"
        )
        return 1

    use_color = sys.stdout.isatty() and not args.no_color and not args.json

    statuses = scan_first_level_directories(
        root_dir=target_path,
        fetch=args.fetch,
        fetch_timeout=args.fetch_timeout,
        git_cmd=DEFAULT_GIT_COMMAND,
        include_hidden=args.include_hidden,
        git_only=args.git_only,
    )

    if args.json:
        import json

        summary_json = generate_json_summary(statuses)
        sys.stdout.write(json.dumps(summary_json, indent=2) + "\n")
        return 0

    sys.stdout.write("\n")
    sys.stdout.write(f"Scanning immediate directories in: {target_path}\n")
    if args.fetch:
        sys.stdout.write(
            "Remote fetch: ENABLED (remote branches updated before inspection)\n"
        )
    else:
        sys.stdout.write(
            "Remote fetch: DISABLED (evaluating against local tracking refs; use -f/--fetch to fetch)\n"
        )
    sys.stdout.write("\n")

    overview_table = format_overview_table(statuses, use_color=use_color)
    sys.stdout.write(overview_table + "\n\n")

    action_summary = format_action_summary(statuses, use_color=use_color)
    sys.stdout.write(action_summary + "\n\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

# Multi-Repository Git Status Checker

A fast, robust utility to inspect the Git status of all first-level subdirectories in a folder. It reports branch tracking, uncommitted modifications, unpushed commits, unpulled updates, and provides a clear categorized action summary.

---

## Quickstart

Run directly from the terminal:

```bash
# Using Python
./check_git_status.py

# Or using the Bash wrapper
./check_git_status.sh
```

To fetch latest remote branches before checking:
```bash
./check_git_status.py --fetch
```

To inspect a specific parent directory other than `.`:
```bash
./check_git_status.py /path/to/projects
```

---

## Features

- **First-Level Inspection**: Inspects all immediate subdirectories without recursive slowdown.
- **Defensive & Non-Interactive**:
  - Automatically sets `GIT_TERMINAL_PROMPT=0` to avoid credential hangs in headless or CI/CD environments.
  - Configurable timeouts for remote operations.
  - Ignores internal caches (`__pycache__`, `.pytest_cache`) by default.
- **Detailed Git State Detection**:
  - **Ahead**: Local commits ready to push (`git push`).
  - **Behind**: Remote commits ready to pull (`git pull`).
  - **Diverged**: Both ahead and behind remote (`git pull --rebase` or merge).
  - **Uncommitted Changes**: Staged, unstaged, untracked, and merge conflicts.
  - **No Upstream**: Branch exists but has no tracking remote configured.
- **Categorized Action Summary**: Grouped lists with ready-to-run terminal commands.
- **JSON Export**: Pass `--json` for machine-readable output.

---

## CLI Options

| Option | Short | Description | Default |
| :--- | :--- | :--- | :--- |
| `target` | | Base directory to inspect | `.` (current folder) |
| `--fetch` | `-f` | Fetch latest remote branches before inspecting status | `False` |
| `--fetch-timeout` | | Timeout in seconds per repository fetch | `8` |
| `--git-only` | `-g` | Only list and summarize valid Git repositories | `False` |
| `--include-hidden` | | Include folders starting with `.` | `False` |
| `--no-color` | | Disable ANSI terminal colors | `False` |
| `--json` | | Output report as a formatted JSON payload | `False` |
| `--help` | `-h` | Show help and exit | |

---

## Testing & Quality Assurance

Run the test suite and static code checkers:

```bash
# Run unit tests
pytest tests/test_check_git_status.py

# Linting & formatting
ruff check check_git_status.py tests/
ruff format --check check_git_status.py tests/

# Static type checking
mypy check_git_status.py tests/

# Complexity & spelling
complexipy check_git_status.py
codespell check_git_status.py tests/ check_git_status.sh
shellcheck check_git_status.sh
```
# git_tools

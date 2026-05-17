# cagent — Concurrent Agent Workflow

> **Status: v1.1** (evaluated 2026-05-15) — Core workflow operational: dispatch, worktree
> isolation, cherry-pick integration, and conflict resolution all verified. 113 extreme tests
> + 61 manual tests passed, 0 failed. See [CHECKLIST.md](CHECKLIST.md) for full breakdown.

A personal code workflow built on Claude Code: one CLI command fans out to multiple
agents working in parallel git worktrees on the same repository. Tasks start in a
flat queue and evolve into a layered pipeline (architect → builders → integrator).
A controller aggregates outputs and produces a single unified commit.

## Quick Start

### Prerequisites

- Python >= 3.11
- `claude` CLI in PATH (Claude Code)
- Git
- **`claude -p` must be able to authenticate** — run `claude -p "hello"` to verify

```bash
# In Claude Code session:
/cagent run tasks/example.txt

# Unix terminal:
./bin/cagent run tasks/example.txt

# Windows terminal:
bin\cagent.cmd run tasks/example.txt

# Cross-platform (recommended):
python -m cagent run tasks/example.txt
```

cagent inherits your Claude Code session's model and credentials automatically
(including proxies like LiteLLM), provided `claude -p` can authenticate in your
environment.

## Commands

| Command | Description |
|---------|-------------|
| `run <tasks-file>` | Run tasks concurrently with multiple agents |
| `status [run-id]` | One-shot dashboard snapshot |
| `watch [run-id]` | Live ANSI dashboard (press `q` to quit) |
| `log <task-id>` | Show events for a task |
| `clean [run-id]` | Clean up worktrees and branches |
| `push <branch>` | Push to origin (requires y/N confirmation) |

### Run Options

```
-j, --jobs N              Concurrency (default: 4)
--base <branch>           Base branch/SHA (default: HEAD)
--squash                  Squash integration into one commit
--keep-worktrees          Keep worktrees after run
--worker-model <id>       Model override for workers
--integrator-model <id>   Model override for integrator
--timeout <sec>           Per-agent timeout (default: 1800)
--quiet                   Only print START/DONE/FAIL events
--resume <run-id>         Resume a previous run, skipping completed tasks
```

### Resuming Failed Runs

If a run is interrupted or has failures, resume it with:

```bash
python -m cagent run tasks/example.txt --resume 2026-05-06T15-22-58
```

Already-completed tasks are skipped. Use `cagent status` to find the run ID.

## Safety

- cagent **never pushes automatically** — only `cagent push` with explicit y/N confirmation
- Workers cannot run `git push`, `git reset --hard`, `rm -rf`, `rm -fr`, or other destructive commands
- All work happens in isolated git worktrees — your working tree is untouched
- Failed tasks preserve their worktree for debugging

## Tasks File Format

One task per line. Empty lines and `#` comments are ignored:

```
# Authentication module
Add login form to settings page
Create JWT token validation middleware

# Billing feature
Implement Stripe checkout flow
```

## Observability

Three levels of monitoring:

1. **Real-time lines** — `cagent run` stdout shows `[HH:MM:SS] task-NNN <activity>`
2. **Live table** — `python -m cagent watch` for ANSI dashboard (or `cagent status` for snapshot)
3. **Detailed replay** — `python -m cagent log <task-id> -f` for full event stream

## Known Issues

- **`claude -p` authentication**: cagent runs a preflight auth check before starting. If it
  fails, diagnostics are printed with suggested fixes. Ensure `claude -p "hello"` works
  standalone. Use `--api-key` to pass an explicit key if needed.
- **No automated tests**: 113 manual/extreme tests pass, but no pytest suite exists yet. This
  is the top priority for v1.x.

## Requirements

- Python >= 3.11
- `claude` CLI in PATH (Claude Code), with `claude -p` able to authenticate
- Git

## Architecture

```
cagent run tasks.txt -j 4
    │
    ├── Worker 1 (claude -p in worktree) → branch cagent/<run>/task-001
    ├── Worker 2 (claude -p in worktree) → branch cagent/<run>/task-002
    └── Worker N (claude -p in worktree) → branch cagent/<run>/task-N
           │
           └── Integrator (cherry-pick + conflict resolution)
                    │
                    └── integration branch (ready to merge)
```

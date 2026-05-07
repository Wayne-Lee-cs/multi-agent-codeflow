# cagent — Concurrent Agent Workflow

A personal code workflow built on Claude Code: one CLI command fans out to multiple
agents working in parallel git worktrees on the same repository. Tasks start in a
flat queue and evolve into a layered pipeline (architect → builders → integrator).
A controller aggregates outputs and produces a single unified commit.

## Quick Start

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

**No model configuration needed** — cagent inherits your Claude Code session's model
and credentials automatically (including proxies like LiteLLM).

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
```

## Safety

- cagent **never pushes automatically** — only `cagent push` with explicit y/N confirmation
- Workers cannot run `git push`, `git reset --hard`, `rm -rf`, or other destructive commands
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

## Requirements

- Python >= 3.11
- `claude` CLI in PATH (Claude Code)
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

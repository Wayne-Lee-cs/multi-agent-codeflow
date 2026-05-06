# Concurrent Agent Workflow

A personal code workflow built on Claude Code: one CLI command fans out to multiple
agents working in parallel git worktrees on the same repository. Tasks start in a
flat queue and evolve into a layered pipeline (architect → builders → integrator).
A controller aggregates outputs and produces a single unified commit.

## Status

Bootstrap. Implementation plan pending (`/ultraplan`).

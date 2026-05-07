---
description: 并发分发 tasks，多 worker 并行 → integrator 汇总；不自动 push
argument-hint: run <tasks-file> [-j N] | watch | status | log <task-id> | push <branch>
---

通过 Bash 调用 `python -m cagent $ARGUMENTS`，把输出原样转给我。

- `run` 是长时命令；执行期间会持续打印 `[time] task-NNN <activity>` 行，请不要截断输出。
- 想看实时表格，用户可在新终端运行 `python -m cagent watch`，或在会话里再问 `/cagent status`（一次性快照，便宜）。
- `push` 命令会要求 y/N 确认，请把交互转给用户而不是替用户回答。

---
description: 并发分发 tasks，多 worker 并行 → integrator 汇总；不自动 push
argument-hint: run <tasks-file> [-j N] | watch | status | log <task-id> | push <branch> | plan <goal> | 或自然语言
---

## 意图识别

先解析 `$ARGUMENTS`，按以下规则映射到实际命令，再通过 Bash 调用 `python -m cagent <映射后的命令>`：

| 用户输入关键词 | 映射到 |
|---------------|--------|
| 包含 `plan`、`计划`、`拆解`、`分解`、`架构` | `plan "<目标>"` |
| 包含 `run`、`执行`、`跑`、`开始` | `run tasks/<文件名>.txt`（若未指定文件，用 `tasks/example.txt`） |
| 包含 `push`、`推`、`发布`、`提交到远程` | `push <branch>`（若未指定分支，先 `git branch --list "cagent/*"` 列出让用户选） |
| 包含 `status`、`状态`、`进度`、`看看` | `status` |
| 包含 `watch`、`监控`、`实时` | `watch` |
| 包含 `log`、`日志` | `log <task-id>` |
| 包含 `clean`、`清理`、`清除` | `clean` |
| 包含 `branches`、`分支` | `branches` |
| 无法识别 | 当作原始参数直接传给 `python -m cagent` |

## 执行规则

- `plan` 是长时命令；architect agent 会分析目标并生成 tasks.md + conventions.md，请不要截断输出。
- `plan` 完成后，提示用户 `cagent run tasks.md` 执行。
- `run` 是长时命令；执行期间会持续打印 `[time] task-NNN <activity>` 行，请不要截断输出。
- 想看实时表格，用户可在新终端运行 `python -m cagent watch`，或在会话里再问 `/cagent status`（一次性快照，便宜）。
- `push` 命令会要求 y/N 确认，请把交互转给用户而不是替用户回答。

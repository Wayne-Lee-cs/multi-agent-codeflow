# Code Architecture Plan — v5.0 (2026-05-21)

> Phase 1-48 completed. 275 pytest pass, 0 failures. Historical details in [ARCHIVE.md](ARCHIVE.md).

## Current Status

**v5.0 已发布** — 核心功能完备，E2E 验证通过，275 自动化测试覆盖。

### v5.0 已完成项

| # | 任务 | 完成阶段 |
|---|------|----------|
| ~~1~~ | dump_state 手动 dict 替代 asdict() | Phase 37-38 |
| ~~2~~ | integrator `_run_claude_agent()` 提取 | Phase 47 |
| ~~3~~ | safety shlex.split token 化 | Phase 48 |
| ~~4~~ | Windows CTRL_BREAK_EVENT 优雅关闭 | Phase 47 |
| ~~5~~ | E2E 测试框架 (fake claude) | Phase 45 + test_e2e.py |

## Remaining Work (v6.0 Candidates)

| # | 任务 | 优先级 | 风险 | 说明 |
|---|------|--------|------|------|
| 1 | Integrator 多策略 (28.2) | P2 | 中 | `--strategy cherry-pick\|merge\|rebase` |
| 2 | Watch WebSocket (28.4) | P3 | 中 | stdlib HTTP + asyncio WS server |
| 3 | Docker 沙箱 (29.1) | P3 | 高 | 完全隔离，解决间接执行绕过 |
| 4 | 异步 I/O 优化 | P3 | 低 | progress.py 异步写入（需引入依赖或 thread pool） |
| 5 | 手动验证 (D.3-D.8) | P3 | 低 | watch/push/worker-model/noop/timeout 6 项 |

## Architecture

```
cagent/
├── cli/                  — 命令入口 (base/run/watch/plan/logcmd/misc)
├── agent.py              — Worker: claude -p 子进程 + worktree 隔离
├── dispatcher.py         — 调度: 依赖图 + wave 并发 + budget 控制
├── integrator.py         — 集成: cherry-pick + 冲突解决 + 多轮验证
├── git_utils.py          — 统一 git helper (sync + async)
├── safety.py             — 沙箱: regex + shlex token + Write 内容扫描
├── progress.py           — Dashboard + 事件流 + token 追踪
├── tasks.py              — Task dataclass + 解析 + 序列化
├── memory.py             — 跨 task 共享上下文
├── worktree.py           — Git worktree 生命周期管理
└── log.py                — 控制台输出格式化
```

## Extension Points
- **safety.py**: `DENY_PATTERNS` 列表 + `_check_command()` → 新增检查只需添加函数
- **integrator.py**: `_run_claude_agent()` 通用函数 → 不同场景传入不同 prompt
- **dispatcher.py**: wave-based scheduling → 依赖图自动排序

## Known Limitations
- Budget overshoot: 并发 task 间 `--max-tokens` 检查非原子
- Safety bypass: 间接执行（编译二进制/非 Bash 解释器）可绕过 hook，完全隔离需 Docker
- Windows: `os.kill(SIGTERM)` 实为 TerminateProcess，CTRL_BREAK_EVENT 为最佳 effort

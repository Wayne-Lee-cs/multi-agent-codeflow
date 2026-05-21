# Code Architecture Plan — v5.0 迭代（Phase 49-54）

> Phase 1-48 completed (249 pytest pass). Historical details in [ARCHIVE.md](ARCHIVE.md).

## What & Why
- **核心目标**: 6 项迭代改进，从测试覆盖、代码重复、性能、安全、平台兼容性全面提升
- **关键设计决策**: 按优先级排列 — 先做高价值低风险项（重构、性能），再做平台特定项

## 任务优先级

| # | 任务 | 优先级 | 风险 | 影响文件 |
|---|------|--------|------|----------|
| 1 | dump_state 性能优化 | P0 | 低 | tasks.py |
| 2 | integrator claude 子进程提取 | P0 | 中 | integrator.py |
| 3 | safety sandbox token 化 | P1 | 中 | safety.py, tests/test_safety.py |
| 4 | 异步 I/O 优化 | P1 | 中 | progress.py, dispatcher.py |
| 5 | Windows 优雅关闭 | P2 | 低 | cli/base.py, agent.py, dispatcher.py |
| 6 | E2E 测试框架 | P2 | 高 | tests/test_e2e.py, tests/conftest.py |

## File Layout

```
cagent/tasks.py          — dump_state: asdict() → 手动 dict
cagent/integrator.py     — 新增 _run_claude_agent() 提取公共逻辑
cagent/safety.py         — shlex.split token 化检查替代纯 regex
cagent/progress.py       — Dashboard 异步写入优化
cagent/cli/base.py       — Windows CTRL_BREAK_EVENT 优雅关闭
cagent/agent.py          — Windows process group 创建
tests/test_safety.py     — 新增 split-flag 测试用例
tests/test_e2e.py        — 假 claude 可执行文件 E2E 测试
tests/conftest.py        — E2E fixtures
```

## Extension Points
**模式: 策略注入 + 注册表**
- safety.py: `DENY_PATTERNS` 列表 + `_check_command()` 函数 → 新增检查逻辑只需添加新的检查函数
- integrator.py: `_run_claude_agent()` 通用函数 → 任何需要调用 claude 的新场景只需传入不同 prompt
- progress.py: `Dashboard._write_backend` 可替换为不同 I/O 后端（文件、内存、网络）

## Portability
- Windows: CTRL_BREAK_EVENT 需 `CREATE_NEW_PROCESS_GROUP` flag
- Unix: SIGTERM 正常工作，不需要 process group
- shlex.split 在 Windows 上行为与 Unix 不同（posix=False），需要处理

## Review Checklist
- [ ] Architecture matches plan
- [ ] Extension points work (prove it)
- [ ] No hardcoded env assumptions
- [ ] Real bugs (null, boundary)

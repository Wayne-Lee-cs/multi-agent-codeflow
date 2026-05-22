# Code Architecture Plan — v6.0 (2026-05-22)

> Phase 1-56 completed. 324 pytest pass, 0 failures. Historical details in [ARCHIVE.md](ARCHIVE.md).

## Current Status

**v6.0 已发布** — 安全加固 + 运行时稳健性 + 性能优化 + 代码质量 + 可观测性，324 自动化测试覆盖。

### v5.1 已完成项

| # | 任务 | 完成阶段 |
|---|------|----------|
| ~~1~~ | dump_state 手动 dict 替代 asdict() | Phase 37-38 |
| ~~2~~ | integrator `_run_claude_agent()` 提取 | Phase 47 |
| ~~3~~ | safety shlex.split token 化 | Phase 48 |
| ~~4~~ | Windows CTRL_BREAK_EVENT 优雅关闭 | Phase 47 |
| ~~5~~ | E2E 测试框架 (fake claude) | Phase 45 + test_e2e.py |

## v6.0 Roadmap

### Phase 52: 安全加固 (P0) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~52.1~~ | ~~API key 安全传递~~ | 低 | `--api-key` 只传给 claude 子进程 env，不污染 `os.environ` |
| ~~52.2~~ | ~~并发运行互斥锁~~ | 中 | `.cagent/run.lock` + `--force` flag，resume 也在锁内 |
| ~~52.3~~ | ~~WebSocket Origin 校验~~ | 低 | `_is_localhost_origin` 检查，非法 Origin 返回 403 |

### Phase 53: 运行时稳健性 (P1) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~53.1~~ | ~~auth 预检缓存~~ | 低 | `.cagent/auth_ok` 5 分钟缓存，`--api-key` 时强制重新验证 |
| ~~53.2~~ | ~~pytest asyncio warning 修复~~ | 低 | `asyncio_default_fixture_loop_scope = "function"` |
| ~~53.3~~ | ~~`server.py` graceful shutdown~~ | 中 | SIGINT/SIGTERM handler，Windows 兼容 |
| ~~53.4~~ | ~~`cli/run.py` 重复 import 清理~~ | 低 | 移除冗余 `from cagent.tasks import dump_state` |
| ~~53.5~~ | ~~`_flush_io` 加显式锁保护~~ | 低 | `threading.Lock` 保护 atomic swap |

### Phase 54: 性能优化 (P1) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~54.1~~ | ~~Dashboard 增量更新~~ | 中 | `_write_dashboard` 只序列化变化的 task；WS 广播 diff 而非全量 |
| ~~54.2~~ | ~~`_cmd_branches` 用 `git for-each-ref`~~ | 低 | 替代逐分支 `git log`，一次性获取所有信息 |
| ~~54.3~~ | ~~`build_shared_context` 版本号缓存~~ | 低 | 写入时递增版本号，取代每次 stat 多个文件的 mtime 查询 |

### Phase 55: 代码质量 (P2) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~55.1~~ | ~~mypy 集成~~ | 低 | `pyproject.toml` 添加 mypy 配置，修复类型标注 |
| ~~55.2~~ | ~~类型标注补全~~ | 低 | `cli/run.py` 等模块的裸 `list`/`dict`/`Callable` 补全泛型参数 |
| ~~55.3~~ | ~~`_execute_run` 拆分~~ | 中 | 拆为 `_dispatch_phase` / `_integrate_phase` / `_summary_phase` |
| ~~55.4~~ | ~~版本号统一~~ | 低 | `pyproject.toml` 与 PLAN.md 版本号同步为 6.0.0 |
| ~~55.5~~ | ~~`_HOOK_SCRIPT` 模板可读性~~ | 低 | `.format()` 双花括号改为 `string.Template` |

### Phase 56: 日志与可观测性 (P2) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~56.1~~ | ~~日志大小限制~~ | 低 | `logs/task-*.log` 和 `events/task-*.jsonl` 超过阈值时截断旧内容 |
| ~~56.2~~ | ~~Dockerfile 提供~~ | 低 | 提供 `Dockerfile` + 文档，用户可在容器内运行 cagent 实现完全隔离 |

### 已移除

| # | 原任务 | 原因 |
|---|--------|------|
| ~~29.1~~ | ~~Docker 沙箱 (sandbox_docker.py)~~ | 架构评估结论：内嵌 Docker 编排使项目臃肿，零依赖是核心优势。改为提供 Dockerfile (56.2) 让用户自行容器化运行 |

### 遗留手动验证 (P3, 不阻塞发布)

| # | 验证项 |
|---|--------|
| D.3 | `cagent watch` TTY 下 1s 刷新表格 + `q` 退出 |
| D.4 | `cagent watch` 非 TTY 下退化为单次 status |
| D.5 | `cagent push` 输入 `n` / 回车 / Ctrl-C → 无 push 发生 |
| D.6 | `--worker-model claude-haiku-4-5` 时 worker 命令行含 `--model` |
| D.7 | 不可执行任务 → 标 noop，integrator 跳过 |
| D.8 | `--timeout 1` → 标 failed，integrator 合入成功部分 |

## Architecture

```
cagent/
├── cli/                  — 命令入口 (base/run/watch/plan/logcmd/misc)
├── agent.py              — Worker: claude -p 子进程 + worktree 隔离
├── dispatcher.py         — 调度: 依赖图 + wave 并发 + budget 控制
├── integrator.py         — 集成: 多策略 (cherry-pick/merge/rebase) + 冲突解决 + 多轮验证
├── git_utils.py          — 统一 git helper (sync + async)
├── safety.py             — 沙箱: regex + shlex token + Write 内容扫描
├── progress.py           — Dashboard + 事件流 + token 追踪
├── tasks.py              — Task dataclass + 解析 + 序列化
├── memory.py             — 跨 task 共享上下文
├── worktree.py           — Git worktree 生命周期管理
├── compat.py             — 跨平台兼容层 (stdin/ANSI/atomic_write)
├── server.py             — WebSocket dashboard server
└── log.py                — 控制台输出格式化
```

## Extension Points
- **safety.py**: `DENY_PATTERNS` 列表 + `_check_command()` → 新增检查只需添加函数
- **integrator.py**: `_run_claude_agent()` 通用函数 → 不同场景传入不同 prompt
- **dispatcher.py**: wave-based scheduling → 依赖图自动排序

## Known Limitations
- Budget overshoot: 并发 task 间 `--max-tokens` 检查非原子
- Safety bypass: 间接执行（编译二进制/非 Bash 解释器）可绕过 hook；完全隔离建议在 Docker 容器内运行 cagent（见 Dockerfile）
- Windows: `os.kill(SIGTERM)` 实为 TerminateProcess，CTRL_BREAK_EVENT 为最佳 effort

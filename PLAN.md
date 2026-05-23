# Code Architecture Plan — v8.0 (2026-05-23)

> Phase 1-62 mostly completed. 407 pytest pass, 0 failures. Historical details in [ARCHIVE.md](ARCHIVE.md).

## Current Status

**v6.0 已发布** — 安全加固 + 运行时稳健性 + 性能优化 + 代码质量 + 可观测性，342 自动化测试覆盖。
**v7.0 大部分完成** — 全面评估发现 19 个新问题。Phase 57-62 大部分完成。407 tests, 65% coverage, mypy 0 errors。
**v8.0 大部分完成** — Phase 63-65 完成。425 tests, 68% coverage, mypy 0 errors, 0 RuntimeWarning。

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

---

## v7.0 Roadmap

### Phase 57: 安全加固 II (P0) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~57.1~~ | ~~Dashboard innerHTML XSS 修复~~ | 高 | ✅ `textContent` + DOM API + 2 tests |
| ~~57.2~~ | ~~`_cmd_resume` 未传递 `api_key`~~ | 高 | ✅ `api_key=getattr(args, "api_key", None)` + 1 test |
| ~~57.3~~ | ~~HTTP 响应安全头~~ | 中 | ✅ nosniff + CSP + 2 tests |

### Phase 58: 安全加固 III (P1) 部分完成

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~58.1~~ | ~~`post_integrate_cmd` 白名单校验~~ | 中 | ✅ `_validate_cmd_str()` 字符白名单 |
| 58.2 | API key 进程参数泄露 | 中 | help 文本已更新，README 待补 |
| ~~58.3~~ | ~~`atomic_write` 临时文件竞争~~ | 低 | ✅ `tempfile.mkstemp` + 1 test |

### Phase 59: Bug 修复 (P0-P1) ✅ 实现完成，测试待补

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~59.1~~ | ~~`_run_lock` Windows 单字节锁~~ | 中 | ✅ seek(0) + 4096 字节 |
| ~~59.2~~ | ~~Dashboard 增量合并不删除过时 task~~ | 低 | ✅ 写完整快照 |
| ~~59.3~~ | ~~`enable_ansi()` 使用 `os.system("")` hack~~ | 低 | ✅ ctypes SetConsoleMode |

### Phase 60: 测试覆盖提升 (P1) 部分完成

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~60.1~~ | ~~`cli/__init__.py` 测试~~ | 低 | ✅ 3 tests |
| ~~60.2~~ | ~~`cli/run.py` 测试~~ | 中 | ✅ 13 tests (dry-run/resume/lock/clean) |
| ~~60.3~~ | ~~`cli/logcmd.py` 测试~~ | 低 | ✅ 3 tests |
| 60.4 | `cli/plan.py` 测试 | 低 | 60.4.1 ✅, 60.4.2 待做: _cmd_plan mock |
| ~~60.5~~ | ~~`__main__.py` 测试~~ | 低 | ✅ 2 tests |
| 60.6 | 整体覆盖率目标 65% → 70% | — | `fail_under` 待提升 |

### Phase 61: 代码质量 (P2) 大部分完成

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~61.1~~ | ~~`_check_tokens` 去重~~ | 低 | ✅ `inspect.getsource()` 动态提取 |
| ~~61.2~~ | ~~`agent.py` GitTimeoutError 重复处理~~ | 低 | ✅ `_git_op` / `_git_op_checked` helper |
| ~~61.3~~ | ~~`cli/run.py` `list[Any]` 类型具化~~ | 低 | ✅ 具体类型 + mypy 0 errors |
| ~~61.4~~ | ~~删除 `src/` dead code~~ | 低 | ✅ src/ 已删除 |
| ~~61.5~~ | ~~添加 `--version` 命令~~ | 低 | ✅ `_get_version()` + argparse |
| 61.6 | 统一日志框架 | 中 | print → logging，3 项待做 |

### Phase 62: 架构优化 (P2-P3) 部分完成

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~62.1~~ | ~~配置值范围校验~~ | 低 | ✅ `_VALID_KEYS` 扩展 + range validation |
| 62.2 | Dashboard 类拆分 | 中 | 3 项待做: EventTracker + DashboardPersister |
| 62.3 | 异步信号处理 | 中 | 2 项待做: 信号移入 async 上下文 |
| ~~62.4~~ | ~~WebSocket CORS 预检~~ | 低 | ✅ OPTIONS + CORS headers |

---

## v8.0 Roadmap

### Phase 63: Bug 修复 (P0) 🔴

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 63.1 | `_commit_result` checkout HEAD 新仓库误判 | **高** | `agent.py:349-352` — `git checkout HEAD -- .claude/` 在路径不存在时返回 failed，导致 task 误判。改为 `check=False` + 忽略非零退出码 |
| 63.2 | `server.py` writer.close() 未 await | 中 | `server.py:420` — finally 中 `writer.close()` 产生 RuntimeWarning。确保同步 close + try/except 捕获 |
| 63.3 | `_run_lock` Windows 解锁可靠性 | 中 | `cli/run.py:82-86` — PID 写入后 seek 位置改变导致 unlock 失败。改为 finally 中先 close fd（自动释放锁）再 unlink |
| 63.4 | `_cmd_resume` worktree 安全检查 | 低 | `cli/run.py:506-510` — resume 清理 worktree 前检查 PID 文件是否活跃，避免破坏正在运行实例 |

### Phase 64: 正确性加固 (P1) 🟡

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 64.1 | `set_task_status` 类型校验 | 低 | `progress.py:379` — 添加 `if status not in _VALID_STATUSES: raise ValueError` 运行时检查 |
| 64.2 | `_cmd_plan` sandbox 残留防护 | 低 | `cli/plan.py` — 注册 `atexit` handler 作为 SIGKILL 后的二次防线 |
| 64.3 | `memory.py` append 跨平台 | 低 | `memory.py:36` — 改为 `path.stat().st_size > 0` 在 open 之前检查，消除 append 模式 seek 歧义 |
| 64.4 | 版本号同步 v8.0 | 低 | `pyproject.toml` version `6.0.0` → `8.0.0`，与 PLAN.md 同步 |
| 64.5 | `_resolve_conflicts` env 一致性 | 低 | `integrator.py:573` — `env_continue` 仅构建一次并缓存，传入所有需要 GIT_EDITOR 的 git 调用 |

### Phase 65: 性能与代码质量 (P2) 🟢

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 65.1 | CLI 测试覆盖提升 | 低 | `cli/plan.py` 22% → 50%+；`cli/base.py` 43% → 60%+；整体 65% → 70%+ |
| 65.2 | 日志截断内存优化 | 低 | `progress.py:195-212` — `_truncate_jsonl_if_large` 改为流式倒序扫描，避免全量加载 |
| 65.3 | `_validate_cmd_str` 文档补充 | 低 | `integrator.py:166` — docstring 明确标注 `post_integrate_cmd` 为 trusted input |
| 65.4 | Dashboard 加载字段兼容 | 低 | `progress.py:240-259` — 加载时过滤 `k in TaskProgress.__dataclass_fields__` |
| 65.5 | 测试 RuntimeWarning 消除 | 低 | `tests/test_server.py` — CORS 测试 mock 修复，消除 4 条 RuntimeWarning |
| 65.6 | DENY_PATTERNS 文档补充 | 低 | `safety.py` — docstring 说明 `git clean -fd` 在 dispatcher 内部使用不受沙箱约束 |

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
- Write 工具内容检查假阳性: 测试/文档中合法提及危险命令会被 sandbox 阻止（defense-in-depth 权衡）
- `--api-key` 进程参数泄露: 值出现在 `ps aux`/`wmic` 进程列表中，推荐使用 `ANTHROPIC_API_KEY` 环境变量代替

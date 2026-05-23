# Code Review — v6.0 评估 (2026-05-21)

## v5.0 迭代回顾（Phase 49-51）

### Self-Critique（第一轮）

```
Suspicion: _check_tokens 在 Windows 上 shlex.split(posix=False) 行为不同，可能误判
Check: 测试用例在 Windows (当前环境) 上运行
Result: FALSE ALARM — 18 个 token 化测试在 Windows 上全部通过

Suspicion: Dashboard 缓冲 I/O 可能丢失最后一批事件
Check: flush() 是否在 set_task_status(final) 和 run 结束时都被调用
Result: FALSE ALARM — set_task_status 对 done/failed/noop 状态调用 _flush_io()，run 结束时 dispatcher 调用 dashboard.flush()

Suspicion: CREATE_NEW_PROCESS_GROUP 可能影响子进程的 stdin/stdout pipe
Check: E2E 测试在 Windows 上用真实子进程运行
Result: FALSE ALARM — 5 个 E2E 测试通过，stdin pipe 正常工作

Suspicion: integrator _run_claude_agent 重构可能改变错误处理语义
Check: 原代码 stdin=None raise RuntimeError，新代码 return None
Result: CONFIRMED (minor) — 行为从异常变为 None 返回，调用方正确处理了 None
```

### 已修复问题（5/5）

| # | 问题 | 严重性 | 位置 | 修复方式 |
|---|------|--------|------|----------|
| 1 | `_check_tokens` 纯环境变量输入无限循环 | BUG | `safety.py:105` | `i += 1` 前加守卫 |
| 2 | `_flush_io` 缓冲区竞态事件丢失 | BUG | `progress.py` | atomic swap |
| 3 | `_run_claude_agent` sandbox 文件泄露到 commit | BUG | `integrator.py:189` | 移至调用方 |
| 4 | Write 工具内容检查假阳性 | 设计权衡 | `safety.py` 文档 | 记录已知限制 |
| 5 | Windows SIGTERM fallback 无效 | BUG | `cli/base.py` | 改用 `taskkill /F /PID` |

---

## v6.0 全面评估 (2026-05-21)

### 评估方法

全量阅读 21 个源码文件 + 15 个测试文件 + 项目配置，运行 293 测试确认全通过。
按安全、正确性、设计、性能、代码质量五个维度分类。

### 发现的问题

#### 安全问题 (3)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| S1 | **P0** | `cli/run.py:213` | `--api-key` 写入全局 `os.environ`，所有子进程可见，可能泄露到日志 | Phase 52.1 — 仅传给 claude 子进程 env |
| S2 | **P0** | `cli/run.py` | 同 repo 多 `cagent run` 实例无互斥，worktree/branch 命名冲突 | Phase 52.2 — `.cagent/run.lock` 文件锁 |
| S3 | **P1** | `server.py` | WebSocket 无 Origin 校验，任何本机进程可连接读取 dashboard 数据 | Phase 52.3 — 校验 localhost Origin |

#### 正确性 Bug (3)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| B1 | **P1** | `cli/run.py:164` | KeyboardInterrupt handler 内重复 `from cagent.tasks import dump_state`（函数顶部已 import） | Phase 53.4 |
| B2 | **P1** | `server.py:629` | `run_dashboard_server` 用 `try/except KeyboardInterrupt`，asyncio 下信号传播不可靠 | Phase 53.3 |
| B3 | **P2** | `progress.py` | `_flush_io` atomic swap 依赖 CPython GIL，free-threaded Python (PEP 703) 下有竞态 | Phase 53.5 |

#### 设计问题 (3)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| D1 | **P1** | `cli/base.py` | `_auth_preflight_check` 每次 run 调真实 API (`claude -p "say hello"`)，浪费 token | Phase 53.1 — 5 分钟缓存 |
| D2 | **P2** | `cli/run.py` | `_execute_run` ~200 行单体函数，包含调度/集成/清理/中断处理 | Phase 55.3 — 拆为三阶段 |
| D3 | **P2** | `safety.py` | `_HOOK_SCRIPT` 用 `.format()` + `{{` 双花括号，可读性差 | Phase 55.5 — 改用 `string.Template` |

#### 性能问题 (3)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| P1 | **P1** | `progress.py` / `server.py` | Dashboard 每次变更构建全量快照 + WS 广播全量 JSON | Phase 54.1 — 增量更新 |
| P2 | **P2** | `cli/misc.py` | `_cmd_branches` 逐分支 `git log`，多分支时 O(N) 子进程 | Phase 54.2 — `git for-each-ref` |
| P3 | **P2** | `memory.py` | `build_shared_context` 每次 stat 所有已完成 task 文件的 mtime | Phase 54.3 — 版本号缓存 |

#### 代码质量 (4)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| Q1 | **P2** | 全局 | 无 mypy/pyright 类型检查工具链 | Phase 55.1 |
| Q2 | **P2** | `cli/run.py` 等 | 多处裸 `list` / `dict` / `Callable` 无泛型参数 | Phase 55.2 |
| Q3 | **P2** | `pyproject.toml` | 版本号 5.0.0 vs PLAN.md 声称 v5.1 不一致 | Phase 55.4 |
| Q4 | **P2** | `pyproject.toml` | pytest `asyncio_default_fixture_loop_scope` 未设置，产生 deprecation warning | Phase 53.2 |

### 架构决策：移除 Docker 沙箱

**原计划**: Phase 29.1 — 内嵌 Docker 容器编排 (`sandbox_docker.py`, `--sandbox docker`)

**评估结论**: 移除。理由：
1. 项目核心价值是轻量编排，零依赖 (stdlib-only) 是关键优势
2. Docker 编排需 300-500 行新代码，是项目中最容易出跨平台 bug 的部分
3. 容器启动延迟（2-5s/task）抵消并发优势
4. 当前 hook 沙箱防的是 agent "误操作"（push/rm -rf），不是防恶意对抗
5. claude CLI 自带 `--permission-mode` 已提供一层隔离

**替代方案**: Phase 56.2 提供 `Dockerfile`，用户可自行在容器内运行整个 cagent。
把沙箱边界推到外面，而非嵌入内部 — 更简洁、更灵活、维护成本更低。

### v6.0 评估总结

| 维度 | 评分 | 说明 |
|------|------|------|
| 功能完备性 | ★★★★★ | 核心功能（调度/集成/监控/安全）全部实现并经 E2E 验证 |
| 测试覆盖 | ★★★★★ | 293 tests, 0 failures, 覆盖所有核心路径 |
| 代码质量 | ★★★★☆ | 经 6 轮审计打磨，少量类型标注不完整 |
| 安全性 | ★★★☆☆ | API key 泄露风险 + 无并发锁是最大短板 |
| 性能 | ★★★★☆ | 异步 I/O 优化已做，全量快照广播可改进 |
| 可维护性 | ★★★★☆ | 架构清晰，`_execute_run` 可进一步拆分 |

**v6.0 主线**: 安全加固 (P0) → 运行时稳健性 (P1) → 性能 (P1) → 代码质量 (P2) → 可观测性 (P2)

---

## v7.0 全面评估 (2026-05-23)

### 评估方法

全量阅读 22 个源码文件（含 `src/` 3 个） + 15 个测试文件 + 项目配置。运行 342 测试全通过，覆盖率 59%，mypy 0 errors。
按安全、正确性、代码质量、架构四个维度分类，共发现 19 个新问题。

### 安全漏洞 (5)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| V1 | **P0** | `server.py:221` | Dashboard `budgetDiv.innerHTML` 使用拼接字符串，篡改 dashboard.json 可导致 XSS | Phase 57.1 — 改为 `textContent` + DOM API |
| V2 | **P1** | `integrator.py:156` | `_run_shell_cmd` 通过 `sh -c`/`cmd /c` 执行 `post_integrate_cmd`，repair prompt 嵌入命令原文，可能被 prompt injection 利用 | Phase 58.1 — 字符白名单校验 |
| V3 | **P1** | `cli/__init__.py:41` | `--api-key` 值出现在进程参数列表中（`ps aux`/`wmic`），其他用户可见 | Phase 58.2 — 文档标注风险 + 推荐 env var |
| V4 | **LOW** | `server.py:31` | `_is_localhost_origin` 允许空 Origin，非浏览器客户端可无条件连接 | 已知设计权衡（docstring 已记录） |
| V5 | **LOW** | `compat.py:54` | `atomic_write` 使用固定 `.tmp` 后缀，并发写同一文件可能竞争 | Phase 58.3 — `tempfile.mkstemp` |

### Bug 与功能缺陷 (5)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| B1 | **P0** | `cli/run.py:539` | `_cmd_resume` 调用 `_execute_run` 未传递 `api_key`，resume 时 `--api-key` 参数被忽略 | Phase 57.2 |
| B2 | **P1** | `cli/run.py:50` | `_run_lock` Windows `msvcrt.locking(..., 1)` 只锁 1 字节，理论上可并发 | Phase 59.1 |
| B3 | **P1** | `progress.py:505` | `_do_write_dashboard` 只做 `existing.update(diff)` 增量合并，永远不删除过时 task | Phase 59.2 |
| B4 | **LOW** | `compat.py:43` | `enable_ansi()` 使用 `os.system("")` hack 触发 VT100 模式 | Phase 59.3 |
| B5 | **LOW** | `src/` | `string_utils.py`/`time_utils.py`/`file_utils.py` 未被任何模块引用，疑似遗留 dead code | Phase 61.4 |

### 代码质量 (5)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| Q1 | **P2** | `safety.py` | `_check_tokens` 在模块层和 `_HOOK_SCRIPT` 字符串中重复实现，任何修改需同步两处 | Phase 61.1 |
| Q2 | **P2** | `agent.py` | 约 8 处完全相同的 `try/except GitTimeoutError` 模式，应提取 helper | Phase 61.2 |
| Q3 | **P2** | `cli/run.py` | `_dispatch_phase` 等函数大量使用 `list[Any]`，应替换为 `list[Task]`/`list[AgentResult]` | Phase 61.3 |
| Q4 | **P2** | 全局 | 大部分模块直接 `print()` 输出，仅 `dispatcher.py` 使用 `logging`，缺乏统一日志框架 | Phase 61.6 |
| Q5 | **P3** | `cli/__init__.py` | 缺少 `--version` 命令，用户无法快速确认安装版本 | Phase 61.5 |

### 架构优化方向 (4)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| A1 | **P2** | `config.py` | 配置值只检查类型不验证值域，`jobs: -1` / `timeout: 0` 被接受 | Phase 62.1 |
| A2 | **P2** | `progress.py` | Dashboard 类 ~250 行，同时负责事件跟踪、异步 I/O、磁盘持久化、事件回调 | Phase 62.2 |
| A3 | **P2** | `cli/run.py` | KeyboardInterrupt 在 `asyncio.run()` 外捕获，async 清理（worktree 删除等）可能不完整 | Phase 62.3 |
| A4 | **P3** | `server.py` | WebSocket 不支持 CORS 预检 OPTIONS 请求 | Phase 62.4 |

### 测试覆盖盲区

| 模块 | 当前覆盖率 | 目标 | 计划 |
|------|-----------|------|------|
| `cli/__init__.py` | 15% | 60%+ | Phase 60.1 |
| `cli/run.py` | 24% | 50%+ | Phase 60.2 |
| `cli/logcmd.py` | 0% | 60%+ | Phase 60.3 |
| `cli/plan.py` | 0% | 40%+ | Phase 60.4 |
| `__main__.py` | 0% | 100% | Phase 60.5 |
| `server.py` | 50% | 65%+ | Phase 57.1 附带 |
| **整体** | **59%** | **70%** | Phase 60.6 |

### v7.0 评估总结

| 维度 | 评分 | v6.0 → v7.0 变化 | 说明 |
|------|------|-------------------|------|
| 功能完备性 | ★★★★★ | — | 核心功能完整，缺 `--version` (P3 minor) |
| 测试覆盖 | ★★★★☆ | ↓ | 342 tests 0 failures，但 CLI 层覆盖严重不足（0-24%），整体 59% 偏低 |
| 代码质量 | ★★★★☆ | — | mypy 0 errors，但存在代码重复（safety）和 Any 类型泛用 |
| 安全性 | ★★★★☆ | ↑ | v6.0 修复了 API key 泄露 + 并发锁 + WS Origin。残余：innerHTML XSS (P0) + resume api_key bug (P0) |
| 性能 | ★★★★☆ | — | 增量 dashboard 已实现，无新性能问题 |
| 可维护性 | ★★★★☆ | — | 架构清晰，Dashboard 类可拆分，日志框架缺统一 |

**v7.0 主线**: P0 安全修复 (57) → P0-P1 Bug 修复 (59) → P1 测试提升 (60) → P1 安全加固 (58) → P2 代码质量 (61) → P2-P3 架构优化 (62)

---

## v7.0 迭代进度 (2026-05-23)

### 已完成项 (Phase 57-62)

| Phase | 完成项 | 测试 |
|-------|--------|------|
| 57.1 | innerHTML → DOM API (textContent + createElement) | — |
| 57.1.2 | XSS 测试 (DOM API 验证) | 2 tests |
| 57.2 | resume api_key 传递 | — |
| 57.2.2 | resume api_key 传递测试 | 1 test |
| 57.3 | HTTP 安全头 (nosniff + CSP) | — |
| 57.3.3 | 安全头测试 (nosniff + CSP) | 2 tests |
| 58.1 | `_validate_cmd_str()` 字符白名单 | 14 tests |
| 58.2 | --api-key help 安全警告 | — |
| 58.3 | atomic_write tempfile.mkstemp | — |
| 58.3.2 | atomic_write 并发测试 | 1 test |
| 59.1 | _run_lock 4096 字节锁 | — |
| 59.2 | Dashboard 全量快照写入 | — |
| 59.3 | enable_ansi ctypes SetConsoleMode | — |
| 60.1 | cli/__init__.py 测试 | 3 tests |
| 60.2 | cli/run.py 测试 (dry-run, resume, lock, clean) | 13 tests |
| 60.3 | cli/logcmd.py 测试 | 3 tests |
| 60.4.1 | cli/plan.py _scan_dir_tree 测试 | 8 tests |
| 60.5 | __main__.py 测试 | 2 tests |
| 61.1 | _check_tokens 去重 (inspect.getsource) | 89 tests pass |
| 61.2 | _git_op / _git_op_checked helper | 14 tests pass |
| 61.3 | cli/run.py 类型具化 (list[Task] etc.) | mypy 0 errors |
| 61.4 | src/ dead code 删除 | — |
| 61.5 | --version 命令 (_get_version) | — |
| 62.1 | 配置值范围校验 (_VALID_KEYS) | — |
| 62.4 | WebSocket CORS OPTIONS 预检 | 4 tests |

### 待完成项

| 优先级 | 项 | 内容 |
|--------|-----|------|
| P1 | 60.4.2 | cli/plan.py _cmd_plan mock 测试 |
| P1 | 60.6 | fail_under 提升到 70% |
| P1 | 58.1.2 | repair prompt cmd_str 转义 |
| P1 | 58.1.3 | 特殊字符命令拒绝测试 |
| P2 | 61.6 | 统一日志框架 (print → logging) |
| P2 | 62.2 | Dashboard 类拆分 |
| P2-P3 | 62.3 | 异步信号处理 |

### 当前指标

- **Tests**: 407 passed, 0 failed
- **Coverage**: 65.49% (target: 70%)
- **mypy**: 0 errors
- **Phase 57-62**: ~27/47 items done

---

## v8.0 全面评估 (2026-05-23)

### 评估方法

全量阅读 22 个源码文件 + 18 个测试文件 + 项目配置。运行 407 测试全通过，覆盖率 65.36%，mypy 0 errors，4 条 RuntimeWarning。
按 Bug/安全、正确性/设计、性能/代码质量三个维度分类，共发现 15 个新问题。

### Bug / 安全 (4)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| B1 | **P0** | `agent.py:349-352` | `_commit_result` 中 `git checkout HEAD -- .claude/` 和 `git checkout HEAD -- .gitignore`，若路径不存在于 HEAD（新仓库首次运行），`_git_op` 返回 `AgentResult(failed)`，导致本应成功的 task 被标记 failed | Phase 63.1 |
| B2 | **P0** | `server.py:420` | `_handle_connection` 的 finally 块中 `writer.close()` 返回的 coroutine 未被 await，产生 4 条 RuntimeWarning；部分路径缺少 `await writer.wait_closed()`，可能导致连接资源泄漏 | Phase 63.2 |
| B3 | **P1** | `cli/run.py:82-86` | `_run_lock` Windows 解锁 `msvcrt.locking(LK_UNLCK)` 在 PID 写入后文件位置可能改变，解锁静默失败；`lock_path.unlink` 在 Windows 文件仍被打开时抛 `PermissionError`，两道防线都可能失效 | Phase 63.3 |
| B4 | **P1** | `cli/run.py:506-510` | `_cmd_resume` 对 pending tasks 执行 `git worktree remove --force` + `git branch -D`，不检查是否有另一个进程在使用这些 worktree；配合 `--force` 跳过互斥锁时可能破坏正在运行实例的 worktree | Phase 63.4 |

### 正确性 / 设计 (5)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| D1 | **P1** | `progress.py:379` | `set_task_status` 的 `status` 参数类型为 `str`，可接受任何值如 `"whatever"`，绕过 `TaskProgress.status` 的 `Literal` 约束，mypy 不报错因 `disallow_untyped_defs = false` | Phase 64.1 |
| D2 | **P1** | `cli/plan.py:44` | `_cmd_plan` 对 `repo_root` 调用 `prepare_sandbox()`，在用户实际 repo 中写入 `.claude/settings.local.json` + hooks；若进程被 SIGKILL，cleanup 不执行，sandbox 文件残留影响后续手动 claude 使用 | Phase 64.2 |
| D3 | **P1** | `memory.py:36` | `RunMemory.append` 在 append 模式打开文件后用 `f.seek(0, 2)` 检查文件大小，但 append 模式下 `seek`/`tell` 在 Windows 和 POSIX 上行为不一致，Python 文档明确标注 "不一定有意义" | Phase 64.3 |
| D4 | **P1** | `pyproject.toml` | 版本号 `6.0.0` 与 PLAN.md/CHECKLIST.md 的 v7.0 不一致，Phase 55.4 同步到 6.0.0 后未跟进 v7.0 | Phase 64.4 |
| D5 | **P2** | `integrator.py:573` | `_resolve_conflicts` 中构建 `env_continue = {**os.environ, "GIT_EDITOR": "true"}` 但仅传入部分 git 操作；`os.environ` 在异步并发中被修改时可能产生竞态（当前不会发生，但属于脆弱设计） | Phase 64.5 |

### 性能 / 代码质量 (6)

| ID | 严重性 | 位置 | 问题 | 修复计划 |
|----|--------|------|------|----------|
| Q1 | **P1** | 测试覆盖 | `cli/plan.py` 22%、`cli/__init__.py` 15%、`cli/base.py` 43%、`cli/logcmd.py` 47%，CLI 层覆盖严重不足 | Phase 65.1 |
| Q2 | **P2** | `progress.py:195-212` | `_truncate_jsonl_if_large` 超 5MB 时 `read_text` 全量加载到内存再截断，大文件场景内存尖峰 | Phase 65.2 |
| Q3 | **P2** | `integrator.py:166` | `_validate_cmd_str` 允许 `'`, `` ` ``, `$`, `\|`, `&`, `;` 等 shell 元字符，注释已说明"不是安全沙箱"，但缺文档说明 `post_integrate_cmd` 是 trusted input | Phase 65.3 |
| Q4 | **P2** | `progress.py:240-259` | `Dashboard.__init__` 加载已有 `dashboard.json` 时无字段版本兼容检查，未来字段变化时 `setattr(tp, k, v)` 静默设置不当值 | Phase 65.4 |
| Q5 | **P2** | `server.py:420` | 4 条 `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited` 来自 CORS 测试，测试 mock 方式有缺陷 | Phase 65.5 |
| Q6 | **P3** | `dispatcher.py:77-81` | `_reset_worktree` 直接执行 `git clean -fd`，与 `safety.py` DENY_PATTERNS 中 `git clean -[a-z]*f` 规则矛盾；虽然 dispatcher 不受沙箱约束，但规则不一致 | Phase 65.6 |

### v8.0 评估总结

| 维度 | 评分 | v7.0 → v8.0 变化 | 说明 |
|------|------|-------------------|------|
| 功能完备性 | ★★★★★ | — | 核心功能完整，`--version` 已添加 |
| 测试覆盖 | ★★★★☆ | ↑ | 407 tests 0 failures，覆盖率 59% → 65%，CLI 层仍偏低 |
| 代码质量 | ★★★★☆ | — | mypy 0 errors，4 条 RuntimeWarning 需消除 |
| 安全性 | ★★★★☆ | — | v7.0 修复 XSS + resume api_key + 安全头；残余：新仓库 checkout 误判 (P0) |
| 性能 | ★★★★☆ | — | 增量 dashboard，大日志截断可优化 |
| 可维护性 | ★★★★☆ | — | 架构清晰，版本号不一致、set_task_status 类型宽松需修复 |

**v8.0 主线**: P0 Bug 修复 (63) → P1 正确性加固 (64) → P2 性能与代码质量 (65)

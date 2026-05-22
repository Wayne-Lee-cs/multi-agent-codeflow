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

### 评估总结

| 维度 | 评分 | 说明 |
|------|------|------|
| 功能完备性 | ★★★★★ | 核心功能（调度/集成/监控/安全）全部实现并经 E2E 验证 |
| 测试覆盖 | ★★★★★ | 293 tests, 0 failures, 覆盖所有核心路径 |
| 代码质量 | ★★★★☆ | 经 6 轮审计打磨，少量类型标注不完整 |
| 安全性 | ★★★☆☆ | API key 泄露风险 + 无并发锁是最大短板 |
| 性能 | ★★★★☆ | 异步 I/O 优化已做，全量快照广播可改进 |
| 可维护性 | ★★★★☆ | 架构清晰，`_execute_run` 可进一步拆分 |

**v6.0 主线**: 安全加固 (P0) → 运行时稳健性 (P1) → 性能 (P1) → 代码质量 (P2) → 可观测性 (P2)

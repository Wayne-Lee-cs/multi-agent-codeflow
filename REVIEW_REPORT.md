# cagent — Code Review & Evaluation Report

> v10.0 并行代码审查完成（2026-05-23）。6 个审查任务中 4 个成功，2 个因 stream 解析限制失败。
> 历史已修复发现归档在 [ARCHIVE.md](ARCHIVE.md)。本文件追踪 **当前 OPEN 问题** 和 **评估分数**。

---

## Current Scores (v10.0, 2026-05-23)

> **评分基准**: 第四次全面评估。576 tests pass, 76% coverage, mypy 0 errors。

| Dimension | Score (1-10) | 说明 |
|-----------|-------------|------|
| 架构设计 | 9 | 清晰分层、零依赖、职责单一 |
| 代码质量 | 8 | 类型完备、防御编程、原子操作 |
| 安全性 | 8 | 多层防御、已知限制记录清晰 |
| 测试充分性 | 7.5 | 量够但核心路径覆盖不足 |
| 可维护性 | 8.5 | 模块化好、文档完善 |
| 跨平台支持 | 8 | Windows/Unix 双路径覆盖 |
| **综合** | **8.2/10** | **生产就绪，测试缺口需补** |

---

## Open Issues

### MEDIUM — 6 items (code review 2026-05-18)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| M1 | cli.py:216 | `_get_repo_root()` 无 try/except，非 git 仓库暴露 traceback | 用户体验差 | try/except + 友好错误 | **FIXED** |
| M2 | integrator.py:359 | async `_run_git()` 无 timeout，git 挂起时永久阻塞 | 进程泄漏 | `asyncio.wait_for(..., timeout=60)` | **FIXED** |
| M3 | log.py:25 | LinePrinter cancel 时 queue 未 flush，最后几条 DONE/FAIL 丢失 | 输出不完整 | break 前 drain queue | **FIXED** |
| M4 | memory.py:62 | `build_shared_context` 缓存不感知内容变化 | integrator append 后返回过期数据 | per-file mtime 加入 cache key | **FIXED** |
| M5 | agent.py | 无 pytest mock 测试，仅靠 E2E 验证 | 回归风险 | Phase 27 补充 | **FIXED** (8 tests) |
| M6 | integrator.py | 无 pytest mock 测试，仅靠 E2E 验证 | 回归风险 | Phase 27 补充 | **FIXED** (14 tests) |

### Code Review Bug Fixes (2026-05-19 后追加)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| CR1 | cli.py:678 | `_write_summary` 从 `tasks` 读 token 而非 `results` | summary token 数据为 0 | 改读 `results` | **FIXED** |
| CR2 | dispatcher.py:20 | `_is_retryable` 子串匹配过于宽泛（如 "timeout" 匹配 "connection_timeout_error"） | 非瞬态错误被重试 | 改用 compiled regex + `\b` word boundary | **FIXED** |
| CR3 | memory.py:71 | `build_shared_context` 迭代 `task_ids` 而非 `sorted_ids` | 缓存 key 与实际迭代不一致 | 改为迭代 `sorted_ids` | **FIXED** |
| CR4 | integrator.py:384 | `proc.kill()` 在进程已退出时抛 `ProcessLookupError` | timeout handler 本身抛异常 | wrap `try/except ProcessLookupError` | **FIXED** |

### NEW — 15 items (深度审计 2026-05-19)

#### P0 — Bug / 正确性 (5 items, Phase 30)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| N1 | memory.py:70 | `_cached_ids` 字段名语义错误，实际存 `(ids, mtimes)` | 重构时引入 bug | 重命名为 `_cache_key` | **FIXED** |
| N2 | dispatcher.py:200 | Kahn 拓扑排序 `list.pop(0)` O(n) | 10+ 任务性能退化 | 改用 `collections.deque` | **FIXED** |
| N3 | agent.py:254 | sandbox 文件删除与 `git add -A` 时序窗口 | sandbox 文件可能被提交 | add 前验证已清除 | **FIXED** |
| N4 | cli.py:505 | run_id UTC 与终端时间显示不一致 | 中国用户调试困惑 | LinePrinter 改用本地时区 | **FIXED** |
| N5 | integrator.py:359 | `_run_git` 每次 `os.environ.copy()` | 内存/CPU 浪费 | 默认 `env=None` | **FIXED** |

#### P1 — 设计 / 健壮性 (6 items, Phase 31)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| N6 | safety.py | 缺 `node -e` / `powershell -Command` / `cmd /c` deny | 高频绕过路径未覆盖 | DENY_PATTERNS 新增 4 条 + pwsh | **FIXED** |
| N7 | agent.py:272 | `git add -A` 可能提交 .env/node_modules/编译产物 | 敏感信息泄露 + 臃肿 commit | 注入标准 .gitignore 排除 | **FIXED** (Phase 37 C2) |
| N8 | cli.py:1182 | Windows `os.kill(SIGTERM)` 实为 `TerminateProcess` | 日志误导 + 非 graceful 停止 | 平台判断 + 修正消息 | **FIXED** (Phase 36 B7) |
| N9 | progress.py:262 | `setattr(tp, k, v)` 无字段验证 | 拼写错误静默创建新属性 | 检查 `__dataclass_fields__` | **FIXED** |
| N10 | dispatcher.py:63 | `_reset_worktree` 缺 `git clean -fd` | 重试拾取上次残留文件 | reset 后追加 clean | **FIXED** |
| N11 | cli.py:921 | `_print_events_formatted` 一次性读全文件 | 大文件内存爆发 | 逐行读取 | **FIXED** |

#### P2 — 代码质量 / 可维护性 (4 items, Phase 32)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| N12 | cli.py | 1452 LOC 过长，9 子命令 + 渲染混合 | 可维护性差 | 拆分为 `cli/` 包 | **FIXED** (Phase 40: 6 submodules) |
| N13 | progress.py | `asdict(tp)` 递归序列化 `last_event.raw` | I/O + CPU 浪费 | 提取 `_task_progress_dict` 共享函数 | **FIXED** (C5 + E2) |
| N14 | pyproject.toml | 缺 `[project.scripts]` 入口 | `pip install -e .` 后无法直接用 `cagent` | 添加 entry point | **FIXED** (Phase 38.3.3) |
| N15 | agent/integrator | Phase 27 优先级不足 | 核心模块 0 测试覆盖 | P2 → P0 提升 | **FIXED** (Phase 27 done) |

### NEW — 10 items (深度审计 2026-05-19 第二轮)

#### P0 — Bug / 正确性 (2 items, Phase 33)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| A1 | agent.py:214-326 | `_commit_result` 中 6 个 git subprocess 调用无 timeout，与 M2 同类 bug | git 挂起时进程永久阻塞（GPG/index.lock/网络） | 提取 `_run_git_async` + timeout=60 | **FIXED** |
| A2 | cli.py:644 | `_cmd_resume` 不传 conventions，恢复运行丢失全局约定 | worker 代码风格不一致 | resume 时重新加载 conventions | **FIXED** |

#### P1 — 设计 / 健壮性 (6 items, Phase 34)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| A3 | agent.py:88 + integrator.py:250 | `os.environ.copy()` 冗余（3.6.5 只修了 `_run_git`） | 每次 agent 调用复制完整 env dict | 改传 `env=None` | **FIXED** |
| A4 | safety.py:42 | `python -c` 模式只拦截含 `subprocess` 的，`os.system` 等绕过 | sandbox 安全漏洞 | 改为 `r"\bpython[3]?\s+-c\b"` 全面阻断 | **FIXED** |
| A5 | integrator.py:331 | cherry-pick continue 前 `git add -A` 可能暂存 sandbox 文件 | sandbox 文件进入 commit | add 前清理 sandbox 文件 | **FIXED** |
| A6 | progress.py:196 | Dashboard 加载路径用 `hasattr`，`set_task_status` 用 `__dataclass_fields__` | 验证不一致，损坏数据可注入属性 | 统一验证方式 | **FIXED** |
| A7 | cli.py:431 | KeyboardInterrupt 不终止 worker 子进程 | 孤儿 claude -p 持续消耗 API credits | 通过 PID 文件 terminate workers | **FIXED** (cli.py:435-443) |
| A8 | cli.py:1206 | `_cmd_plan` 运行 architect 无 safety sandbox | plan agent 可执行破坏性命令 | 注入临时 sandbox，完成后清理 | **FIXED** |

#### P2 — 代码质量 (2 items, Phase 35)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| A9 | tests/ | `AsyncLineIterator` + `_make_process` 在 test_agent 和 test_integrator 中重复定义 | 维护成本 | 提取到 conftest.py | **FIXED** (conftest.py:16-67, test files import) |
| A10 | agent.py:146 | `last_lines.pop(0)` O(n)，与 N2 (dispatcher deque 修复) 不一致 | 影响极小（max 5 元素），但不一致 | 改用 `deque(maxlen=5)` | **FIXED** (agent.py:134) |

### NEW — 8 items (第三轮审计 2026-05-19)

#### P0 — Bug / 正确性 (1 item, Phase 36)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| B1 | cli.py:1225 | `_cmd_plan` sandbox 注入后无 finally 清理，sandbox 文件残留用户 repo | 后续 claude 会话行为被 sandbox hook 干扰 | try/finally 包裹 + finally 删除 sandbox 文件 | **FIXED** |

#### P1 — 设计 / 健壮性 (4 items, Phase 36)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| B2 | integrator.py:335 | `_resolve_conflicts` 中 `os.environ.copy()` 残留，只为设 GIT_EDITOR | 每次冲突解决复制完整 env dict | `{**os.environ, "GIT_EDITOR": "true"}` | **FIXED** |
| B3 | dispatcher.py:73-87 | `_reset_worktree` 的 git reset/clean 无 timeout | 重试时 index.lock 竞争可能挂起 | `asyncio.wait_for(..., timeout=60)` | **FIXED** |
| B4 | test_agent.py:17-34 | fixture 重复定义 shadow conftest（Phase 35 只去重 helper 未去重 fixture） | conftest 修改不反映到 test_agent | 删除 test_agent 重复 fixture | **FIXED** |
| B5 | REVIEW/CHECKLIST | A7/A9/A10 及 34.2.4 状态标记与代码不符 | 维护者误判项目状态 | 同步文档（本轮已修正） | **FIXED** |

#### P2 — 代码质量 (3 items, Phase 36)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| B6 | log.py | 0 pytest 覆盖，核心控制台模块无自动化验证 | 回归风险 | 补充 LinePrinter 单元测试 | **FIXED** (13 tests) |
| B7 | cli.py:1210 | Windows `_terminate_pid` 平台差异（跨 N8/31.2.2/34.2.5 三次标记未修） | Windows 下 cancel 非 graceful | 平台判断 + 修正日志 | **FIXED** (merged N8) |
| B8 | Phase 32 | cli.py 拆包 / Dashboard 序列化 / pip install 三项 P2 | 可维护性 + 用户体验 | 见 Phase 32 + 37 + 38 + 40 | **FIXED** (32.1 Phase 40, 32.2 C5+E2, 32.3 E7) |

### NEW — 5 items (第四轮审计 2026-05-19)

#### P1 — 设计 / 健壮性 (2 items, Phase 37)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| C1 | integrator.py:154-160 | `_cherry_pick_one` 中 cherry-pick subprocess 无 timeout，同模块 `_run_git` 已有 timeout | git cherry-pick 挂起时进程永久阻塞（GPG/submodule/网络） | 改用 `_run_git("cherry-pick", ...)` 替代裸 subprocess | **FIXED** |
| C2 | agent.py:319 | `git add -A` 可能提交 `.env`/`node_modules`/编译产物（承接 N7/31.2.1） | 敏感信息泄露 + 臃肿 commit | worktree `.gitignore` 注入标准排除规则 | **FIXED** |

#### P2 — 代码质量 / 性能 (3 items, Phase 37)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| C3 | dispatcher.py:220-222 | Kahn 排序下游查找 O(N*M)，每次遍历全部 task | 50+ task 场景性能退化 | 预建 `children` 邻接表 | **FIXED** |
| C4 | cli.py | 0 pytest 覆盖 — 唯一高频修改但无自动化测试的模块（1426 LOC） | 回归风险最高 | 优先给纯函数写 mock 测试 | **FIXED** (tests/test_cli.py 8 passing) |
| C5 | progress.py:296 | `get_snapshot` 用 `asdict(tp)` 递归序列化含大量 raw（承接 N13/32.2） | 长任务 CPU/内存开销 | 手动构建 dict 替代 | **FIXED** |

### NEW — Additional findings from code review (Phase 37 iteration 2)

#### P0 — Correctness Bug (1 item)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| D1 | integrator.py:348 | `_resolve_conflicts` 成功返回 True 但从未更新 `task.commit_sha` 或 `task.status`，后续集成检查 `t.status=="done" and t.commit_sha` 会跳过该 task | 冲突解决后的 task 在后续集成迭代中被静默跳过 | 在 `return True` 前执行 `result = await _run_git("rev-parse", "HEAD", ...)` 并更新 `task.commit_sha` 和 `task.status="done"` | **FIXED** |

#### P2 — Code Quality (1 item)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| D2 | dispatcher.py:283 | `dump_state` 在标记 blocked tasks 循环内调用 N 次，应只在所有 blocked 任务标记完成后调用一次 | 冗余 I/O + 中间状态可能泄露 | 将 `dump_state` 移至循环外，条件触发 | **FIXED** |

### NEW — 7 items (第五轮审计 2026-05-19)

#### P0 — Bug / 正确性 (1 item, Phase 38)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| E1 | agent.py:67 | `gitignore_path.write_text()` 覆写 worktree 原有 `.gitignore`，agent 运行期间用户自定义排除规则丢失 | 被排除的文件可能意外参与 agent 运行 + 潜在敏感文件泄露 | 改为追加模式：读取现有内容后追加 cagent 排除规则块 | **FIXED** |

#### P1 — 设计 / 健壮性 (3 items, Phase 38)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| E2 | progress.py:325 | `_write_task_progress` 仍用 `asdict(tp)` 递归序列化 `last_event.raw`（C5 只修了 `get_snapshot`） | per-task progress 写入路径序列化大量 raw 数据 | 提取 `_task_progress_dict` 共享函数，两处复用 | **FIXED** |
| E3 | cli.py:157 | `_auth_preflight_check` 中 `env=os.environ.copy()` 残留 | 与 Phase 34 全局 `env=None` 策略不一致 | 移除该参数 | **FIXED** |
| E4 | dispatcher.py:73-97 | `_reset_worktree` 用裸 `create_subprocess_exec`，与 agent/integrator 辅助函数模式不一致 | 三套 git 调用模式增加维护成本 | 复用 `agent._run_git_async` | **FIXED** |

#### P2 — 代码质量 (3 items, Phase 38)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| E5 | CHECKLIST | 31.2.1/31.2.2/34.2.5 标 open 但已被后续 Phase 修复 | 维护者误判项目状态 | 同步标记（本轮已修正） | **FIXED** |
| E6 | pyproject.toml | 版本号 `2.1.0` 与实际 v3.4 不一致 | 用户/工具获取错误版本信息 | 更新为 `3.4.0` | **FIXED** |
| E7 | pyproject.toml | 缺 `[project.scripts]` 入口（承接 N14/32.3，反复推迟） | 无法 `pip install -e .` 后直接使用 `cagent` | 添加 `cagent = "cagent.cli:main"` | **FIXED** |

#### Code review regression fixes (Phase 38 iteration 2)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| R1 | dispatcher.py:18-26 | `_is_retryable` regex 用 `\b` word boundary，`_` 是 word char 导致 `network_timeout` 等下划线连接形式不匹配 | 可重试错误被误判为不可重试 | `\bnetwork`/`\bconnection` 去掉尾部 `\b`，匹配前缀形式 | **FIXED** |
| R2 | progress.py:292-349 | `get_snapshot` 与 `_write_task_progress` 重复 dict 构建，新增字段需同步两处 | 维护成本 + 静默 diverge 风险 | 提取 `_task_progress_dict` 共享函数 | **FIXED** |
| R3 | agent.py:67 | `.gitignore` 块开头 `\n` 导致文件不存在时首行为空行 | 纯 cosmetic（git 忽略） | 条件化前缀 + 常量提升为模块级 | **FIXED** |

### NEW — 8 items (第六轮审计 2026-05-19)

#### P0 — Bug / 正确性 (2 items, Phase 39)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| F1 | memory.py:33-35 | `append()` 中 `f.tell()` 在 append 模式下平台相关不可靠，某些系统返回 0 即使文件非空 | 多次 append 的内容缺少分隔符，粘连在一起 | 改为 `f.seek(0, 2)` 在 open 上下文内原子检查 | **FIXED** |
| F2 | safety.py + agent.py | `.gitignore` 双重写入：`prepare_sandbox` 追加 `.claude/`，`run_agent` 追加 cagent exclusion block，两处独立写入可能产生冗余 | gitignore 累积重复行（功能不受影响） | 统一由 `agent.py` 负责 `.gitignore` 写入 + `_cmd_plan` 独立注入 | **FIXED** |

#### P1 — 设计 / 健壮性 (4 items, Phase 39)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| F3 | safety.py:42 | `python[3]?\s+-c\b` 缺少前导 `\b`，`cpython -c`/`ipython -c` 也被匹配 | 与其他 `\b` 锚定模式风格不一致 | 改为 `r"\bpython[3]?\s+-c\b"` | **FIXED** |
| F4 | dispatcher.py:158 | `_reset_worktree` 失败被 `except Exception: pass` 静默吞掉 | retry 在脏 worktree 运行，可能提交残留文件 | 改为 `logging.warning(...)` | **FIXED** |
| F5 | progress.py:333 | `_append_event` 用 `asdict(event)` 序列化完整 raw 到 events.jsonl | 长任务 events.jsonl 文件巨大 | 手动 dict 排除 raw 字段 + 移除未用 asdict import | **FIXED** |
| F6 | __main__.py:14 | version check 仅在 `__main__` 执行，pip install 入口点 `cli:main` 跳过检查 | Python < 3.11 用户得到语法错误而非友好提示 | `cli.main()` 开头调用（编码修复在前） | **FIXED** |

#### P2 — 代码质量 (2 items, Phase 39)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| F7 | pyproject.toml | 缺少 `authors`/`license`/`readme` 发布元数据 | PyPI 发布需要 | 补充基础元数据 + 版本号 3.5.0 | **FIXED** |
| F8 | tests/ | `_cmd_cancel`/`_cmd_clean` 等 CLI 子命令无 mock 测试 | 回归风险 | 补充 8 个 mock 测试 | **FIXED** |

#### Code review regression fixes (Phase 39 iteration 2)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| G1 | cli.py:_cmd_plan | F2 移除 `prepare_sandbox` 的 `.gitignore` 写入后 plan 命令失去保护 | sandbox 文件可能被 `git add -A` 意外提交 | `_cmd_plan` 独立注入 + cleanup 恢复原始内容 | **FIXED** |
| G2 | cli.py:main() | F6 版本检查置于 Windows 编码修复之前 | Windows GBK 控制台可能输出乱码 | 调换顺序 | **FIXED** |
| G3 | memory.py:append() | F1 `path.stat()` TOCTOU 竞态 | 并发 append 可能丢失分隔符 | 改为 `f.seek(0, 2)` 在 open 上下文内检查 | **FIXED** |

### Phase 40 — Evaluation Pass (2026-05-19)

Full re-read of all 13 modules post-Phase 39. **No new P0/P1 issues found.** Code is stable.

| # | Type | Finding | Status |
|---|------|---------|--------|
| H1 | P2 | `test_agent.py::test_run_git_async_timeout` RuntimeWarning: `AsyncMock.kill()` returns unawaited coroutine | **FIXED** (Phase 42: AsyncMock→MagicMock for sync calls) |
| H2 | docs | CHECKLIST 32.2/32.3/28.3 stale open markers for items completed in Phase 37-38 | **FIXED** (cross-refs synced) |

### Phase 41 — `--post-integrate-cmd` Multi-round Validation (2026-05-19)

| # | Module | Change | Status |
|---|--------|--------|--------|
| J1 | cli/__init__.py | `--post-integrate-cmd` argparse flag | **DONE** |
| J2 | cli/run.py | Pass `post_integrate_cmd` to `integrate()` | **DONE** |
| J3 | integrator.py | `_run_shell_cmd`: cross-platform shell command execution with timeout | **DONE** |
| J4 | integrator.py | `_post_integrate_validate`: run cmd → fail → repair agent → retry (max 2 rounds) | **DONE** |
| J5 | tests/test_integrator.py | 7 new tests: shell_cmd success/fail/timeout + validate pass/repair/fail/agent-fail | **DONE** |

### Phase 43 — Resource Limit (2026-05-20)

| # | Module | Change | Status |
|---|--------|--------|--------|
| K1 | cli/__init__.py | `--max-turns N` and `--max-tokens N` argparse flags | **DONE** |
| K2 | agent.py | `max_turns` param → `--max-turns` pass-through to claude -p | **DONE** |
| K3 | dispatcher.py | `max_tokens` budget enforcement: check after each task, fail remaining when exceeded | **DONE** |
| K4 | dispatcher.py | `nonlocal budget_exceeded` fix — missing `nonlocal` caused `UnboundLocalError` in all tasks | **DONE** (bug found during testing) |
| K5 | cli/run.py | Pass max_turns/max_tokens to dispatcher, write budget.json, update banner + summary | **DONE** |
| K6 | cli/watch.py | `_load_budget()` + budget percentage display + yellow warning at ≥80% | **DONE** |
| K7 | tests/ | 7 new tests: agent max-turns (2) + dispatcher budget (3) + dashboard budget display (2) | **DONE** |

### Phase 44 — Bug Fix K8-K14 (2026-05-20)

| # | Module | Issue | Severity | Fix | Status |
|---|--------|-------|----------|-----|--------|
| K8 | dispatcher.py | Transitive dependency blocking — single-pass blocked marking misses A→B→C chains | P0 | `while True` closure loop for transitive closure + `test_transitive_blocked_tasks` | **FIXED** |
| K9 | progress.py | `fail_reason` persists after retry-then-success; dashboard shows stale error | P1 | Clear `fail_reason` when status set to `done`/`noop` | **FIXED** |
| K10 | cli/run.py | Resume crashes if `base_sha` file missing from run_dir | P1 | Fallback to `current_head()` + stderr warning | **FIXED** |
| K11 | cli/misc.py | `_cmd_cancel` leaves PID file after successful terminate | P1 | `pid_path.unlink(missing_ok=True)` after terminate + ProcessLookupError | **FIXED** |
| K12 | cli/watch.py | `_print_dashboard_table` emits raw ANSI escapes when piped to file/non-TTY | P2 | `use_color = sys.stdout.isatty()` conditional for all ANSI output | **FIXED** |
| K13 | cli/run.py | `if args.max_turns:` falsy for value 0, inconsistent with `is not None` elsewhere | P2 | Unified to `is not None` checks | **FIXED** |
| K14 | cli/run.py | `_write_summary` token counts miss prior-run tokens on resume | P2 | Fallback to dashboard.json cumulative totals, take max of both sources | **FIXED** |

### 重新评估 — 新发现 (2026-05-20 全量代码审读)

#### P0 — E2E 验证缺失 (项目最大风险)

| # | 问题 | 影响 | 状态 |
|---|------|------|------|
| V1 | 所有 `claude -p` 交互均为 mock，E2E 从未成功 | prompt 格式、stream-json 解析、worktree 并发、cherry-pick 链路、budget enforcement 全部未验证 | **FIXED** (Phase 45: real E2E + test_e2e.py fake claude framework) |

#### P1 — 设计 / 正确性

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| V2 | integrator.py:290-296 | repair commit 可能为空 — agent 未改文件时 `git commit` 静默失败（`check=False`） | 浪费一轮 repair round，validation 命令永远失败 | commit 前 `git status --porcelain` 检查 | **FIXED** (Phase 47) |
| V3 | dispatcher.py:183-186 | budget check 与并发 task 竞态 — `budget_exceeded` flag 对同时运行的 task 不立即可见 | 实际 token 可超预算 `(concurrency-1)` 个 task | 文档注明 + help 说明 | **FIXED** (Phase 48: --max-tokens help + Known Limitations) |
| V4 | cli/run.py:150-183 | KeyboardInterrupt 在 `asyncio.run()` 中断后同步调 `_clean_worktrees` | event loop 状态不确定，可能残留 worktree | 最小化 handler，清理交给 `cagent clean` | **FIXED** (Phase 48) |
| V5 | — | 零用户文档，无 README.md | 用户无法了解安装和使用方式 | 写 README.md | **FIXED** (Phase 46) |

#### P2 — 代码质量

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| V6 | 全局 | 3 套 git helper：`worktree._git`(sync) / `agent._run_git_async`(async tuple) / `integrator._run_git`(async GitResult) | 接口不一致，维护成本高 | 提取 `cagent/git_utils.py` 统一 | **FIXED** (Phase 47) |
| V7 | cli/run.py:441 | `import json as _json2` 和 `import json as _json` 同函数重复 import | 代码混乱 | 移至模块顶部 | **FIXED** (Phase 47) |
| V8 | agent.py:289 | `task.prompt.split("\n")[0][:72]` — prompt 以 `\n` 开头时 commit message 为空 | 空 commit message | `.strip().split(...)` | **FIXED** (Phase 47) |
| V9 | PLAN.md | 500行增量变更日志，非架构路线图 | 难以快速了解项目方向 | 精简为架构+状态+milestones，历史归档 | **FIXED** (Phase 46) |
| V10 | safety.py | 仅拦截 Bash 工具，Edit/Write 可写恶意脚本后 Bash 执行 | 沙箱可绕过 | PreToolUse 增加 Write 内容检查 | **FIXED** (Phase 48) |

### LOW — 6 items (deferred, non-blocking)

| # | Module | Issue | Status |
|---|--------|-------|--------|
| L1 | integrator.py | 空 prompt — 首个 task 与 base 冲突时 merged_summaries 为空 | **FIXED** (Phase 42: fallback prompt) |
| L2 | cli.py | run_id 1 秒分辨率，理论碰撞 | 实践中不可能 |
| L3 | safety.py | 间接执行绕过（bash x.sh） | 3.7.1 部分修复，完全解决需 Docker |
| L4 | agent.py | API Key 在 os.environ 中可见 | CLI 标准做法 |
| L5 | cli.py | architect prompt injection | 用户即作者，无第三方场景 |
| L6 | cli.py | Windows `os.kill(SIGTERM)` 实为 TerminateProcess，worker 无 graceful stop | CTRL_BREAK_EVENT 实现 (Phase 47)，fallback 纳入 Known Limitations |

### INFO — Unverified scenarios (need manual test)

| # | Scenario | Reason |
|---|----------|--------|
| I1 | `cagent watch` TTY ANSI 刷新 + q 退出 | 需要交互式终端 |
| I2 | `cagent watch` 非 TTY 退化 | 需要 pipe 环境 |
| I3 | `cagent push` 拒绝场景 | 需要远程 repo |
| I4 | `--worker-model` 实际传递 | 需要有效 API key |
| I5 | `--timeout 1` 强制超时 | 需要运行中的 claude |

---

## Test Coverage Matrix (v5.0, 275 tests)

| Module | pytest | Gap |
|--------|--------|-----|
| tasks.py | 22 | ✅ |
| safety.py | 67 | ✅ |
| progress.py | 40 | ✅ |
| compat.py | 7 | ✅ |
| worktree.py | 8 | ✅ |
| dispatcher.py | 26 | ✅ |
| memory.py | 17 | ✅ |
| agent.py | 14 | ✅ |
| integrator.py | 29 | ✅ |
| log.py | 13 | ✅ |
| cli/ | 21 | ✅ |
| e2e | 5 | ✅ (fake claude framework) |
| git_utils | 6 | ✅ |
| **Total** | **275** | — |

---

## Benchmark (latest)

| Mode | Time | Tasks | Speedup |
|------|------|-------|---------|
| Single Agent (serial) | 47.7s | 4 | — |
| cagent (j=4 parallel) | 16.7s | 4 | **2.86x** |

Speedup scales with task weight and count. 4 lightweight tasks is near the lower bound.

---

## Open Issues — 并行代码审查 (2026-05-23)

> cagent 并行审查：6 个任务，4 个成功（task-001/002/003/005），2 个失败（task-004 dispatcher+integrator, task-006 test infra）。
> 以下为 **新发现的 OPEN 问题**，尚未修复。

### HIGH — 4 items

| # | Module | Issue | Impact | Fix |
|---|--------|-------|--------|-----|
| T1 | git_utils.py:45/84 | `run_git` 超时抛 `RuntimeError`，`run_git_async` 超时抛 `GitTimeoutError`，异常类型不一致 | 调用者无法用统一 except 捕获超时 | `run_git` 也抛 `GitTimeoutError` | **FIXED** |
| T2 | safety.py:226-229 | `_HOOK_SCRIPT` 用 `.replace()` 模板注入，`__PATTERNS_JSON__` 和 `__CHECK_TOKENS_SOURCE__` 二次替换可能互相干扰 | 未来修改 DENY_PATTERNS 若包含模板标记字面量会破坏 hook | 改用 `string.Template` | **FIXED** |
| T3 | server.py:24-39 | `_is_localhost_origin` 未校验 scheme，`file://localhost` 或自定义 scheme 可绕过 | 非浏览器客户端可构造任意 Origin 绕过检查 | 添加 `parsed.scheme in ("http", "https")` | **FIXED** |
| T18 | cli/run.py:34-88 | `_run_lock` 文件锁在进程被 kill -9 后残留，`--force` 完全跳过锁机制 | 残留锁文件可能误导用户；`--force` 允许真正并发运行 | 获取锁前检查 PID 活跃性，清理过期锁 | **FIXED** |

### MEDIUM — 16 items

| # | Module | Issue | Impact | Fix |
|---|--------|-------|--------|-----|
| T4 | compat.py:46-51 | `enable_ansi()` 中 `ctypes.windll.kernel32` 调用未检查返回值 | CI/管道环境可能抛未处理 `OSError` | `try/except OSError` 包裹 Windows 分支 |
| T5 | git_utils.py:80-81 | `run_git_async` 超时时 `proc.kill()` 在 Windows 上不杀子进程 | 超时场景残留僵尸进程 | Windows 上用 `taskkill /T` 或渐进 terminate→kill |
| T6 | memory.py:10 | `_validate_agent_id` 缺少 null byte (`\x00`) 检查 | 安全校验层不完整 | 添加 `"\x00" in agent_id` 检查 |
| T7 | memory.py:37/44-47/55 | `write()`/`append()`/`read()` 均未处理 `OSError` | 磁盘错误导致未预期异常传播 | `append()` 至少加 `try/except OSError` |
| T8 | safety.py:33-62 | `DENY_PATTERNS` 不覆盖绝对路径调用（如 `/usr/bin/git push`） | 绝对路径可绕过所有 deny 模式 | `_check_tokens` 中解析 PATH 或添加绝对路径模式 |
| T9 | server.py:538-539 | close 帧未解析状态码，未回送带状态码的 close 帧 | RFC 6455 协议不合规 | 解析 close 帧 2 字节状态码并回送 |
| T10 | server.py:760-766 | 信号处理使用 `asyncio.ensure_future()`（Python 3.10+ 已废弃） | 未来 Python 版本可能移除 | 替换为 `asyncio.create_task()` |
| T19 | cli/base.py:143 | `_print_auth_diagnostics` 泄露 API key 前 8 位和后 4 位 | 共享终端/日志收集中泄露凭据 | 仅输出 `(set, length=N)` |
| T20 | cli/base.py:67-76 | `_auth_preflight_check` 缓存文件无并发保护 | 多进程竞争写 auth_ok | 使用 `atomic_write` |
| T21 | cli/base.py:196-204 | `_is_pid_active` 在 Windows 上可能因 PID 复用误判 | 终止不相关进程 | 使用 `GetExitCodeProcess` 检查 |
| T22 | cli/run.py:44-46 | `_run_lock` 的 `force=True` 完全跳过锁，允许真正并发 | worktree 和 git 分支冲突 | `--force` 应获取锁失败时警告但继续 |
| T23 | cli/run.py:367-371 | `_cmd_run_inner` 直接调用 `subprocess.run` 而非 `git_utils` | 违反全局 git 操作统一规范 | 封装到 `git_utils.py` |
| T24 | cli/plan.py:14-35 | `_scan_dir_tree` 无 symlink 循环保护 | 符号链接循环导致无限递归 | 检查 `entry.is_symlink()` 并跳过 |
| T25 | cli/plan.py:87 | `_cleanup_sandbox` 在 `_cmd_plan` 结尾执行两次（atexit + finally） | atexit 被 unregister 后无兜底 | 不在 finally 中 unregister |
| T26 | cli/logcmd.py:48-60 | `_follow_file` 文件被删除后进入无限空读循环 | 无法检测文件消失 | 添加文件存在性检查 |
| T27 | cli/misc.py:24-25 | `_cmd_clean --all --force` 无二次确认 | 误操作永久删除所有运行记录 | `--all --force` 要求输入 "yes" |

### LOW — 20 items

| # | Module | Issue |
|---|--------|-------|
| T11 | compat.py:69-73 | `atomic_write` 中 `os.replace()` 跨卷会失败 |
| T12 | compat.py:69-73 | Unix 上 `mkstemp` 创建文件权限 `0o600`，替换后继承 |
| T13 | git_utils.py:56 | `cwd` 不存在时误报 "'git' not found" |
| T14 | safety.py:73-74 | `shlex.split` 失败时直接返回安全，引号错误可绕过 split-flag 检测 |
| T15 | config.py:15 | `strategy` 只检查类型，不验证合法值 |
| T16 | config.py:55-56 | TOML 解析错误被静默吞掉 |
| T17 | server.py:379-385 | HTTP 请求行未校验方法白名单和版本格式 |
| T28 | cli/__init__.py:153-160 | `__getattr__` 中 `ImportError` 未转换为 `AttributeError` |
| T29 | cli/__init__.py:13-15 | `_get_version` 的 `except Exception` 过于宽泛 |
| T30 | cli/base.py:221-222 | `_terminate_pid` 的 `CTRL_BREAK_EVENT` 可能影响其他进程 |
| T31 | cli/base.py:157-164 | `_get_repo_root` 未捕获 `FileNotFoundError` |
| T32 | cli/run.py:586-595 | `_write_summary` 每次从磁盘重新读取 dashboard.json |
| T33 | cli/plan.py:103-107 | `goal` 参数未经转义直接嵌入 prompt |
| T34 | cli/logcmd.py:65-67 | `_print_event_line` 静默吞掉 JSON 解析错误 |
| T35 | cli/logcmd.py:79-89 | 颜色代码硬编码，无 `--no-color` 选项 |
| T36 | cli/watch.py:97 | `_load_budget` 每次刷新时重新读取文件 |
| T37 | cli/watch.py:96 | ANSI 清屏在非 ANSI 终端上产生乱码 |
| T38 | cli/watch.py:136-142 | 列宽固定，长 task ID 被截断 |
| T39 | cli/misc.py:168-192 | `_cmd_cancel` 未检查任务状态，PID 复用风险 |
| T40 | cli/misc.py:148-150 | `_cmd_push` 非交互终端 EOFError 时退出原因不明确 |

### 跨模块问题

| # | 严重程度 | Issue | 涉及文件 |
|---|---------|-------|---------|
| X1 | MEDIUM | 多处直接调用 `subprocess.run` 进行 git 操作，违反统一规范 | run.py:367,509,516; misc.py:68,80,88,114,131,139,155,198 |
| X2 | LOW | 错误处理风格不一致（sys.exit(1) vs sys.exit(130) vs 静默返回） | 全部 CLI 模块 |

### 未覆盖的审查范围

| 范围 | 原因 | 状态 |
|------|------|------|
| dispatcher.py + integrator.py (Task 004) | stream 解析错误 "chunk longer than limit" | 需手动审查或重跑 |
| test infrastructure (Task 006) | 同上 | 需手动审查或重跑 |

### 汇总

| 严重程度 | OPEN | FIXED |
|---------|------|-------|
| HIGH | 0 | 4 |
| MEDIUM | 16 | 0 |
| LOW | 20 | 0 |
| 跨模块 | 2 | 0 |
| **总计** | **38** | **4** |

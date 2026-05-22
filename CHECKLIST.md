# cagent — Implementation Checklist

> v2.1 (Phase 1-24) completed: 153/165 items (92.7%). Details in [ARCHIVE.md](ARCHIVE.md).
> v3.0-4.0 (Phase 25-48) completed. **275 pytest all pass.**
> **v5.0 (2026-05-21)**: Phase 25-48 全部提交。E2E 验证通过，git_utils 统一，Write 安全扫描，Windows 优雅关闭。
> **v5.1 (2026-05-21)**: Phase 49-51 完成。多策略集成、WebSocket dashboard、异步 I/O。293 tests。
> **v6.0 (2026-05-22)**: Phase 52-56 全部完成。324 tests。mypy 0 errors。
> This file tracks only **remaining** and **new** work.

---

## Remaining from v2.1 (8 items, all LOW/deferred)

### Deferred — acceptable for current version
- [x] **D.1** `integrator.py` — Empty prompt when first task conflicts with base (edge case) *(Phase 42 修复：fallback prompt "conflicts with base branch")*
- [x] **D.2** `cli.py` — run_id timestamp collision at 1-second resolution *(Phase 51 修复：添加 `%f` 微秒)*

### Unverified — need manual testing
- [ ] **D.3** `cagent watch` TTY 下 1s 刷新表格 + `q` 退出
- [ ] **D.4** `cagent watch` 非 TTY 下退化为单次 status
- [ ] **D.5** `cagent push` 输入 `n` / 回车 / Ctrl-C → 无 push 发生
- [ ] **D.6** `--worker-model claude-haiku-4-5` 时 worker 命令行含 `--model`
- [ ] **D.7** 不可执行任务 → 标 noop，integrator 跳过
- [ ] **D.8** `--timeout 1` → 标 failed，integrator 合入成功部分

---

## Phase 25: v3.0 — Bug 修复 (P0, 代码审查 2026-05-18)

### 25.0 代码审查发现的 Bug

- [x] **25.0.1** `cli.py:216` — `_get_repo_root()` 的 `subprocess.run(..., check=True)` 在非 git 仓库内暴露 traceback。包裹 try/except，输出 `"Error: not inside a git repository."`
- [x] **25.0.2** `integrator.py:359-388` — async `_run_git()` 无 timeout。添加 `asyncio.wait_for(proc.communicate(), timeout=60)` + `TimeoutError` → kill + raise
- [x] **25.0.3** `log.py:25-33` — `LinePrinter.run()` 收到 `CancelledError` 直接 break，queue 剩余事件丢失。break 前 flush：`while not self._queue.empty(): self._print_line(*self._queue.get_nowait())`
- [x] **25.0.4** `memory.py:62-64` — `build_shared_context` 缓存 key 不含内容变化时间，integrator append 后缓存过期。改为 per-file mtime 加入 cache key

---

## Phase 26: v3.0 — 可靠性加固 (P1)

### 26.1 自动重试
- [x] **26.1.1** `Task` dataclass 新增 `retry_count: int = 0` / `max_retries: int = 0` 字段
- [x] **26.1.2** `cli.py` — `--retries N` flag（默认 0）
- [x] **26.1.3** `dispatcher.py` — `_run_one` 失败后检查 `fail_reason`，可重试类错误（timeout/rate-limit/network）指数退避重试
- [x] **26.1.4** `tasks.py` — `retry_count` 字段通过 `dump_state` 自动持久化
- [x] **26.1.5** 测试：4 个新用例（timeout→重试成功、重试耗尽、不可重试、retry_count 递增）

### 26.2 Token 追踪
- [x] **26.2.1** `progress.py` — `EventParser._parse_event` 解析 `result` 事件的 `usage` 字段
- [x] **26.2.2** `progress.py` — `TaskProgress` 新增 `tokens_in: int` / `tokens_out: int`，`Dashboard.update()` 累加
- [x] **26.2.3** `cli.py` — `status` 表格 + summary.md 展示 token 消耗
- [x] **26.2.4** 测试：7 个新用例（usage 解析、累加、持久化、恢复）

### 26.3 单 Task 取消
- [x] **26.3.1** `agent.py` — `run_agent` 启动后写 PID 文件，退出时清理（try/finally）
- [x] **26.3.2** `cli.py` — `cagent cancel <task-id>` 子命令，读 PID 文件 → SIGTERM + 更新 dashboard
- [x] **26.3.3** dashboard 更新为 failed (reason: cancelled by user)
- [x] **26.3.4** 测试：cancel 命令向 PID 发信号 *(Phase 42 完成：TestTerminatePid 4 tests — SIGTERM/PermissionError/ProcessLookupError/dashboard)*

---

## Phase 27: v3.0 — 测试补全 (P2)

### 27.1 agent.py mock 测试
- [x] **27.1.1** `tests/test_agent.py` — mock subprocess：正常完成 → commit → done
- [x] **27.1.2** 超时场景：terminate → kill → failed
- [x] **27.1.3** 非零退出码 → failed + fail_reason 含 stderr
- [x] **27.1.4** stdin pipe 错误 → failed
- [x] **27.1.5** git commit 失败 → failed
- [x] **27.1.6** conventions + shared_context 注入验证

### 27.2 integrator.py mock 测试
- [x] **27.2.1** `tests/test_integrator.py` — cherry-pick 成功路径
- [x] **27.2.2** cherry-pick 冲突 → integrator agent 解决 → continue
- [x] **27.2.3** integrator agent 失败 → abort → 返回 False
- [x] **27.2.4** squash 模式验证
- [x] **27.2.5** partial integration（部分 task 失败）

### 27.3 端到端验收
- [ ] **27.3.1** watch TTY 实际验证（手动）
- [ ] **27.3.2** push 拒绝场景验证（手动）
- [ ] **27.3.3** noop + timeout 混合场景（手动）

---

## Phase 28: v3.0 — 功能增强 (P3)

### 28.1 Integrator 多轮验证
- [x] **28.1.1** `cli.py` — `--post-integrate-cmd "pytest"` flag *(Phase 41 完成)*
- [x] **28.1.2** `integrator.py` — cherry-pick 完成后执行用户指定命令 *(Phase 41 完成)*
- [x] **28.1.3** 命令失败 → 给 integrator agent 修复 prompt → 第二轮 *(Phase 41 完成)*
- [x] **28.1.4** 最多重试 2 轮，仍失败则标记 integration 为 partial *(Phase 41 完成)*

### 28.2 Integrator 多策略
- [x] **28.2.1** `cli.py` — `--strategy cherry-pick|merge|rebase` flag（默认 cherry-pick）*(Phase 49 完成)*
- [x] **28.2.2** `integrator.py` — `_merge_strategy()` / `_rebase_strategy()` 实现 *(Phase 49 完成)*
- [x] **28.2.3** 测试各策略的冲突/无冲突路径 *(Phase 49 完成, 5 新测试)*

### 28.3 pip install 支持
- [x] **28.3.1** `pyproject.toml` — 添加 `[project.scripts] cagent = "cagent.cli:main"` *(Phase 38 E7/38.3.3 完成)*
- [x] **28.3.2** 验证 `pip install -e .` 后 `cagent run --help` 可用 *(Phase 38 验证)*
- [x] **28.3.3** 保持 `python -m cagent` 仍然可用 *(Phase 38 验证)*

### 28.4 Watch WebSocket (P4)
- [x] **28.4.1** `cagent/server.py` — stdlib HTTP + asyncio WebSocket server *(Phase 50 完成)*
- [x] **28.4.2** Dashboard 变化时推送 JSON 到 WebSocket clients *(Phase 50 完成)*
- [x] **28.4.3** `cagent watch --web [port]` flag 启动 server *(Phase 50 完成)*
- [x] **28.4.4** 简单 HTML 前端（可选）*(Phase 50 完成, 内嵌 HTML/CSS/JS)*

---

## Phase 29: v3.0 — 安全演进 (P4, 长期)

### ~~29.1 Docker 沙箱~~ — **已移除 (v6.0 评估)**
> 架构评估结论：内嵌 Docker 编排使项目臃肿（+300-500行），零依赖是核心优势。
> 替代方案：Phase 56.2 提供 Dockerfile，用户可自行在容器内运行 cagent 实现完全隔离。
- [x] ~~**29.1.1**~~ `cagent/sandbox_docker.py` — **REMOVED**: 不再实现
- [x] ~~**29.1.2**~~ `--sandbox docker` flag — **REMOVED**: 不再实现
- [x] ~~**29.1.3**~~ Volume mount worktree — **REMOVED**: 不再实现
- [x] ~~**29.1.4**~~ Fallback — **REMOVED**: 不再实现

### 29.2 Resource Limit ✅ (Phase 43 完成)
- [x] **29.2.1** `--max-turns N`：per-task turn limit, pass-through to `claude -p --max-turns` *(Phase 43)*
- [x] **29.2.2** `--max-tokens N`：per-run token budget (input+output combined), checked after each task completes; exceeding budget fails remaining tasks with "token budget exceeded" *(Phase 43)*
- [x] **29.2.3** Dashboard + summary display budget percentage; yellow ANSI warning at ≥80%; `budget.json` persisted for status/watch *(Phase 43)*
- [x] **29.2.4** `dispatcher.py` — `nonlocal budget_exceeded` scoping fix (without it, all dispatcher tests crashed with `UnboundLocalError`) *(Phase 43)*
- [x] **29.2.5** Tests: 7 new (2 agent max-turns + 3 dispatcher budget + 2 dashboard budget display) *(Phase 43)*

---

## Phase 30: v3.0 — 代码审计 Bug 修复 (P0, 2026-05-19)

### 30.1 正确性 Bug
- [x] **30.1.1** `memory.py:70` — 字段名 `_cached_ids` 语义错误，实际存储 `(ids, mtimes)` 元组。重命名为 `_cache_key`
- [x] **30.1.2** `dispatcher.py:200` — Kahn 拓扑排序 `queue.pop(0)` O(n)，改用 `collections.deque`
- [x] **30.1.3** `agent.py:254-260` — sandbox 文件删除与 `git add -A` 时序窗口。`git add -A` 前验证 sandbox 文件已清除
- [x] **30.1.4** `cli.py:505` — run_id UTC 与终端时间显示不一致。`LinePrinter` / `_print_event_line` 改用本地时区
- [x] **30.1.5** `integrator.py:359` — `_run_git` 每次调用 `os.environ.copy()` 浪费开销。改为默认 `env=None`，subprocess 自动继承

---

## Phase 31: v3.0 — 代码审计 设计加固 (P1, 2026-05-19)

### 31.1 安全加固
- [x] **31.1.1** `safety.py` — DENY_PATTERNS 新增 `node -e`、`powershell -Command`、`cmd /c`、`deno eval/run` 模式
- [x] **31.1.2** `safety.py` — 新增模式的 pytest 用例（至少 4 个：各命令 + 命令链组合）

### 31.2 健壮性
- [x] **31.2.1** `agent.py:272` — `git add -A` 可能提交意外文件（.env/node_modules/编译产物）。注入标准排除规则或检查 *(Phase 37 C2 修复：worktree .gitignore 注入标准排除规则)*
- [x] **31.2.2** `cli.py:1182` — Windows `os.kill(SIGTERM)` 实为 `TerminateProcess`。平台判断 + 修正日志消息 *(Phase 36 B7 修复：_terminate_pid 平台判断 + 修正日志)*
- [x] **31.2.3** `progress.py:262` — `setattr(tp, k, v)` 无字段验证。检查 `k in TaskProgress.__dataclass_fields__`
- [x] **31.2.4** `dispatcher.py:63-70` — `_reset_worktree` 缺少 `git clean -fd`，untracked 文件残留
- [x] **31.2.5** `cli.py:921` — `_print_events_formatted` 一次性读取整个文件到内存。改用逐行读取

---

## Phase 32: v3.0 — 代码质量优化 (P2, 2026-05-19)

### 32.1 结构优化
- [x] **32.1.1** `cli.py` 拆分为 `cli/` 包：`base.py`、`run.py`、`watch.py`、`plan.py`、`logcmd.py`、`misc.py` *(Phase 40 完成)*
- [x] **32.1.2** `cli/__init__.py` 保持 `main()` 入口不变，向后兼容 `python -m cagent` + `pip install` *(Phase 40 完成)*

### 32.2 性能优化
- [x] **32.2.1** `progress.py` — Dashboard 序列化排除 `last_event.raw`，减少 I/O *(Phase 37 C5 + Phase 38 E2 + R2 完成)*
- [x] **32.2.2** `progress.py` — 手动 dict 构建替代 `asdict(tp)` 递归序列化 *(Phase 37 C5 + Phase 38 E2 完成)*

### 32.3 安装体验
- [x] **32.3.1** `pyproject.toml` — 添加 `[project.scripts] cagent = "cagent.cli:main"` *(Phase 38 E7/38.3.3 完成)*
- [x] **32.3.2** 验证 `pip install -e .` 后 `cagent run --help` 可用 *(Phase 38 验证)*
- [x] **32.3.3** 保持 `python -m cagent` 仍然可用 *(Phase 38 验证)*

### 32.4 测试优先级提升
- [x] **32.4.1** Phase 27 (`agent.py` + `integrator.py` mock 测试) 优先级从 P2 → P0，应优先于 Phase 28 功能开发

---

## Phase 33: v3.1 — 深度审计 Bug 修复 (P0, 2026-05-19 第二轮)

### 33.1 正确性 Bug

- [x] **33.1.1** `agent.py:214-326` — `_commit_result` 中 6 个 git subprocess 调用无 timeout（`git status`, `git checkout` x2, `git add`, `git commit`, `git rev-parse`）。与 25.0.2（integrator `_run_git` timeout）同类遗漏。提取 `_run_git_async(cmd, cwd, timeout=60)` 辅助函数替换裸 `create_subprocess_exec`
- [x] **33.1.2** `agent.py` — 新增 `_run_git_async` 的 pytest 测试（timeout 场景 + 正常场景）
- [x] **33.1.3** `cli.py:644` — `_cmd_resume` 调用 `_execute_run` 不传 conventions。从原始 tasks_file 重新加载 conventions，或在 run_dir 中持久化 conventions 内容（run 时写入 `run_dir/conventions.txt`，resume 时读取）
- [x] **33.1.4** `cli.py` — `_cmd_run` 中将 conventions 内容持久化到 `run_dir/conventions.txt` 以支持 resume

---

## Phase 34: v3.1 — 深度审计 设计加固 (P1, 2026-05-19 第二轮)

### 34.1 安全加固

- [x] **34.1.1** `safety.py:42` — `python[3]?\s*-c.*subprocess` 改为 `r"\bpython[3]?\s+-c\b"` 全面阻断（与 node -e 策略一致）
- [x] **34.1.2** `safety.py` — 更新对应 pytest 用例：`python -c "import os; os.system(...)"` 也被拦截
- [x] **34.1.3** `integrator.py:331` — cherry-pick continue 前 `git add -A` 之前删除 sandbox 文件（复用 `_commit_result` 的清理模式：删除 `settings.local.json` + `cagent-guard.py`）
- [x] **34.1.4** `cli.py:1206` — `_cmd_plan` 在 repo_root 注入临时 sandbox，architect agent 完成后清理 sandbox 文件

### 34.2 健壮性

- [x] **34.2.1** `agent.py:88` — 移除 `env = os.environ.copy()`，改传 `env=None` 给 `create_subprocess_exec`（subprocess 自动继承父进程环境）
- [x] **34.2.2** `integrator.py:250` — 同上，`_resolve_conflicts` 移除 `env = os.environ.copy()`
- [x] **34.2.3** `progress.py:196` — Dashboard 加载路径的 `hasattr(tp, k)` 改为 `k in TaskProgress.__dataclass_fields__`（与 `set_task_status` line 266 一致）
- [x] **34.2.4** `cli.py:431` — KeyboardInterrupt handler 增加 worker 子进程清理：遍历 `run_dir/pids/*.pid`，对每个活跃 PID 发 terminate 信号 *(代码已实现 cli.py:435-443，标记补正)*
- [x] **34.2.5** `cli.py:1182` — Windows `os.kill(SIGTERM)` 实为 `TerminateProcess`。平台判断 + 修正日志消息（合并 N8） *(Phase 36 B7 修复)*

---

## Phase 35: v3.1 — 深度审计 代码质量 (P2, 2026-05-19 第二轮)

### 35.1 测试去重

- [x] **35.1.1** `tests/conftest.py` — 提取 `AsyncLineIterator` 和 `_make_process` helper 为共享 fixture
- [x] **35.1.2** `tests/test_agent.py` — 移除内联 `AsyncLineIterator`/`_make_process`，改用 conftest import
- [x] **35.1.3** `tests/test_integrator.py` — 同上

### 35.2 一致性修复

- [x] **35.2.1** `agent.py:146` — `last_lines.pop(0)` 改用 `collections.deque(maxlen=5)`（与 dispatcher deque 修复一致）

---

---

## Phase 36: v3.2 — 第三轮审计 (2026-05-19)

### 36.1 正确性 Bug (P0)

- [x] **36.1.1** `cli.py:1225` — `_cmd_plan` 调用 `prepare_sandbox(Path("."))` 后无 finally 清理。sandbox 文件（`.claude/settings.local.json` + `.claude/hooks/cagent-guard.py`）残留在用户 repo 根目录。用 try/finally 包裹整个 plan 流程，finally 中删除 sandbox 文件
- [x] **36.1.2** 文档状态同步 — REVIEW_REPORT A7/A9/A10 与 CHECKLIST 34.2.4 状态与代码实际不符。统一更新为正确状态（代码均已实现，文档已同步）

### 36.2 设计 / 健壮性 (P1)

- [x] **36.2.1** `integrator.py:335` — `_resolve_conflicts` 中 `env_continue = os.environ.copy()` 只为设置 `GIT_EDITOR=true`，每次冲突解决复制完整 env。改用 `{**os.environ, "GIT_EDITOR": "true"}`
- [x] **36.2.2** `dispatcher.py:73-87` — `_reset_worktree` 中 `git reset --hard` 和 `git clean -fd` 无 timeout。与 agent/integrator 的 timeout 策略不一致。改用 `asyncio.wait_for(..., timeout=60)` 或复用 `_run_git_async`
- [x] **36.2.3** `test_agent.py:17-34` — `tmp_worktree`、`tmp_run_dir`、`sample_task` fixture 与 conftest.py 完全重复。删除 test_agent.py 中的定义，使用 conftest 版本

### 36.3 代码质量 (P2，承接未完成项)

- [x] **36.3.1** `log.py` — 0 pytest 覆盖，核心控制台输出模块无自动化测试（13 个测试用例）
- [x] **36.3.2** `cli.py:1210` — Windows `_terminate_pid` 平台行为差异（跨 N8/31.2.2/34.2.5 标记未修），平台判断 + 修正日志
- [x] **36.3.3** Phase 32 未启动项提醒：cli.py 拆包 (32.1)、Dashboard 序列化优化 (32.2)、pip install (32.3) *(全部 DONE: 32.1 Phase 40, 32.2 C5+E2, 32.3 E7)*

---

## Phase 37: v3.3 — 第四轮审计 (2026-05-19)

### 37.1 设计 / 健壮性 (P1)

- [x] **37.1.1** `integrator.py:154-160` — `_cherry_pick_one` 中 `asyncio.create_subprocess_exec("git", "cherry-pick", ...)` 无 timeout。改用 `_run_git("cherry-pick", task.commit_sha, cwd=worktree_path, check=False)` 替代裸 subprocess
- [x] **37.1.2** `agent.py` — worktree `.gitignore` 注入标准排除规则（`.env`/`node_modules`/`__pycache__`/`*.pyc`/`.venv`），在 `prepare_sandbox` 后、`git add -A` 前执行

### 37.2 代码质量 / 性能 (P2)

- [x] **37.2.1** `dispatcher.py:220-222` — Kahn 排序下游查找 `for t in tasks: if tid in t.depends_on` 每次 O(N)。预建 `children: dict[str, list[str]]` 邻接表
- [x] **37.2.2** `cli.py` — 0 pytest 覆盖，用户入口无自动化测试。优先给 `_write_summary`、`_fmt_elapsed`、`_print_dashboard_table` 写 mock 测试
- [x] **37.2.3** `progress.py:296` — Dashboard `get_snapshot` 用 `asdict(tp)` 递归序列化含大量 `last_event.raw`。手动构建 dict 替代（承接 N13/32.2）

---

## Phase 38: v3.4 — 第五轮审计 (2026-05-19)

### 38.1 正确性 Bug (P0)

- [x] **38.1.1** `agent.py:67` — `gitignore_path.write_text()` 覆写 worktree 原有 `.gitignore`，agent 运行期间用户自定义排除规则丢失。改为追加模式：先读取现有内容，追加 cagent 排除规则块（带标记注释便于清理）

### 38.2 设计 / 健壮性 (P1)

- [x] **38.2.1** `progress.py:325` — `_write_task_progress` 仍用 `asdict(tp)` 递归序列化 `last_event.raw`。Phase 37 C5 只修了 `get_snapshot`，此处遗漏。提取 `_task_progress_dict` 共享函数，两处复用
- [x] **38.2.2** `cli.py:157` — `_auth_preflight_check` 中 `env=os.environ.copy()` 残留，与 Phase 34 全局 `env=None` 策略不一致。移除该参数
- [x] **38.2.3** `dispatcher.py:73-97` — `_reset_worktree` 用裸 `create_subprocess_exec` + 手动 kill/wait，与 agent/integrator 的辅助函数模式不一致。复用 `agent._run_git_async`

### 38.3 代码质量 (P2)

- [x] **38.3.1** CHECKLIST 交叉引用同步 — 31.2.1/31.2.2/34.2.5 已被后续 Phase 修复，标记更正为 FIXED *(本轮已修正)*
- [x] **38.3.2** `pyproject.toml` 版本号 `2.1.0` → `3.4.0`，与实际代码版本同步
- [x] **38.3.3** `pyproject.toml` 缺 `[project.scripts]` 入口，添加 `cagent = "cagent.cli:main"`（承接 32.3，反复推迟的 P2 项）

---

## Phase 39: v3.5 — 第六轮审计 (2026-05-19)

### 39.1 正确性 Bug (P0)

- [x] **39.1.1** `memory.py:33-35` — `append()` 中 `f.tell()` 在 append 模式下平台相关不可靠。改为 `f.seek(0, 2)` 在 open 上下文内原子检查
- [x] **39.1.2** `safety.py` + `agent.py` — `.gitignore` 双重写入。统一由 `agent.py` 负责 `.gitignore` 写入，`prepare_sandbox` 移除 `.gitignore` 写入逻辑

### 39.2 设计 / 健壮性 (P1)

- [x] **39.2.1** `safety.py:42` — `python[3]?\s+-c\b` 缺少前导 `\b`。改为 `r"\bpython[3]?\s+-c\b"`
- [x] **39.2.2** `dispatcher.py:158` — worktree reset 失败静默 `pass`。改为 `logging.warning`
- [x] **39.2.3** `progress.py:333` — `_append_event` 用 `asdict(event)` 序列化完整 raw。改为手动 dict 排除 raw 字段，移除未用 `asdict` import
- [x] **39.2.4** `__main__.py:14` — version check 通过 pip install 入口不执行。在 `cli.main()` 开头调用（编码修复先于版本检查）

### 39.3 代码质量 (P2)

- [x] **39.3.1** `pyproject.toml` — 补充 `authors`、`license`、`readme`、`classifiers`、`urls` 发布元数据，版本号 → 3.5.0
- [x] **39.3.2** 测试覆盖 — `_cmd_cancel` (3 tests) / `_cmd_clean` (3 tests) / version check (2 tests) mock 测试

### 39.4 代码审查回归修复

- [x] **39.4.1** `cli.py:_cmd_plan` — F2 移除 `prepare_sandbox` 的 `.gitignore` 写入后，plan 命令失去对 `.claude/` 的 gitignore 保护。在 `_cmd_plan` 中独立注入 + cleanup 时恢复原始内容
- [x] **39.4.2** `cli.py:main()` — 版本检查置于 Windows 编码修复之前。调换顺序确保错误信息正常输出
- [x] **39.4.3** `memory.py:append()` — 从 `path.stat()` 改为 `f.seek(0, 2)` 消除 TOCTOU 竞态

---

## Phase 44: v3.9 — Bug Fix (K8-K14, 代码审查 2026-05-20)

### 44.1 正确性 Bug (P0)

- [x] **44.1.1** `dispatcher.py` — 依赖图 blocked-pass 只做一次，A→B→C 链中 C 未被传递标记为 blocked。改为 `while True` 闭包循环实现 transitive closure。新增 `test_transitive_blocked_tasks` 测试

### 44.2 设计 / 健壮性 (P1)

- [x] **44.2.1** `progress.py` — `set_task_status` 设置 `done`/`noop` 时未清除 `fail_reason`，重试成功后 dashboard 残留错误信息
- [x] **44.2.2** `cli/run.py` — resume 时 `base_sha` 文件不存在直接崩溃，改为 fallback `current_head()` + warning
- [x] **44.2.3** `cli/misc.py` — `_cmd_cancel` 成功 terminate 后 PID 文件残留，添加 `unlink(missing_ok=True)`

### 44.3 代码质量 (P2)

- [x] **44.3.1** `cli/watch.py` — `_print_dashboard_table` 无条件输出 ANSI escape codes。添加 `use_color = sys.stdout.isatty()` 条件控制
- [x] **44.3.2** `cli/run.py` — `if args.max_turns:` truthiness 检查对值 0 错误。统一改为 `is not None`
- [x] **44.3.3** `cli/run.py` — `_write_summary` token 仅从 results 读取，resume 场景不准确。添加 dashboard.json 累计 token fallback

---

## Phase 45: v4.0 — E2E 验证 (2026-05-20)

### 45.1 认证

- [x] **45.1.1** `claude -p "say hello" --output-format stream-json --verbose` 返回正常 JSON ✅

### 45.2 最小 E2E run

- [x] **45.2.1** 2 task 无依赖 `-j 2` — worktree 创建 → agent 执行 → commit → cherry-pick → integration branch ✅
- [x] **45.2.2** stream-json 事件格式与 EventParser 匹配，dashboard 实时更新 ✅

### 45.3 依赖链 E2E

- [x] **45.3.1** 3 task 依赖链 A→B→C — wave-based scheduling 正确执行 ✅
- [x] **45.3.2** cherry-pick 冲突自动解决 — integrator agent 修复 2 次冲突 ✅
- [x] **45.3.3** integration branch 包含所有 3 个 task 的 commit ✅

### 45.4 Budget enforcement E2E

- [x] **45.4.1** `--max-tokens 10000` — task 001 完成后 budget exceeded，task 002/003 标记 failed ✅
- [x] **45.4.2** dashboard 显示 budget 百分比 (230%) ✅

---

## Phase 47: v4.0 — 技术债清理 (2026-05-20)

### 47.1 Git helper 统一

- [x] **47.1.1** `cagent/git_utils.py` — 新增 `run_git()` (sync) + `run_git_async()` (async) + `GitResult` dataclass
- [x] **47.1.2** `worktree.py` — 移除内联 `_git()`，改用 `git_utils.run_git`
- [x] **47.1.3** `agent.py` — `_run_git_async` 改为 thin wrapper 委托 `git_utils.run_git_async`
- [x] **47.1.4** `integrator.py` — `_run_git` 改为 thin wrapper，移除 `_GitResult` 类

### 47.2 代码质量

- [x] **47.2.1** `cli/run.py` — 重复 `import json as _json` / `_json2` 移至模块顶部统一 `import json`
- [x] **47.2.2** `integrator.py:290` — repair commit 前检查 `git status --porcelain`，无变更时跳过 commit
- [x] **47.2.3** `agent.py:289` — commit message `task.prompt.strip().split("\n")[0]` 处理前导空行

### 47.3 测试更新

- [x] **47.3.1** `tests/test_agent.py` — mock 路径 `cagent.agent.asyncio` → `cagent.git_utils.asyncio`

---

## Phase 48: v4.0 — 设计改进 (2026-05-20)

### 48.1 Budget 竞态文档

- [x] **48.1.1** `cli/__init__.py` — `--max-tokens` help 注明 "budget is checked between tasks, concurrent tasks may overshoot"

### 48.2 KeyboardInterrupt handler 清理

- [x] **48.2.1** `cli/run.py` — handler 移除 `_clean_worktrees` 调用，改为提示 `cagent clean <run-id>`

### 48.3 Safety sandbox Write 工具覆盖

- [x] **48.3.1** `safety.py` — hook 脚本新增 `tool_name == "Write"` 分支，检查 `tool_input["content"]`
- [x] **48.3.2** `safety.py` — `settings.local.json` matcher 新增 `{"matcher": "Write", ...}`
- [x] **48.3.3** `tests/test_safety.py` — 2 个新测试：Write 危险内容拦截 + Write 安全内容放行 + 现有测试添加 `tool_name` 字段

---

## Phase 46: v4.0 — 用户文档 (2026-05-20)

- [x] **46.1** `README.md` — 更新至 v3.9：安装/快速开始/命令参考/配置选项/已知限制
- [x] **46.2** `PLAN.md` — 精简为架构+状态+milestones，Phase 25-44 历史归档至 ARCHIVE.md

---

## Progress Summary

| Phase | Items | Status |
|-------|-------|--------|
| v2.1 Remaining (D.1-D.8) | 8 | deferred/unverified |
| Phase 25 (Bug 修复) | 4 | **DONE** |
| Phase 26 (可靠性) | 13 | **DONE** (12/13, 1 手动验证) |
| Phase 27 (测试补全) | 14 | **DONE** (11/14, 3 手动验证) |
| Phase 28 (功能增强) | 14 | **DONE** (14/14) — 28.1 multi-round (4/4), 28.2 多策略 (3/3), 28.3 pip install (3/3), 28.4 WebSocket (4/4) |
| Phase 29 (安全演进) | 7 | TODO — 长期 |
| Phase 30 (审计 Bug 修复) | 5 | **DONE** |
| Phase 31 (审计 设计加固) | 7 | **DONE** (7/7, 31.2.1→C2, 31.2.2→B7) |
| Phase 32 (审计 代码质量) | 8 | **DONE** (8/8, 32.1 Phase 40 完成) |
| Phase 33 (深度审计 Bug) | 4 | **DONE** |
| Phase 34 (深度审计 加固) | 9 | **DONE** (9/9, 34.2.5→B7) |
| Phase 35 (深度审计 质量) | 4 | **DONE** |
| Phase 36 (第三轮审计) | 8 | **DONE** (8/8, 36.3.3 Phase 32 全部完成) |
| Phase 37 (第四轮审计) | 5 | **DONE** |
| Phase 38 (第五轮审计) | 7 | **DONE** |
| Phase 39 (第六轮审计) | 11 | **DONE** |
| Phase 42 (Quick Wins) | 6 | **DONE** (H1 warnings + D.1 empty prompt + 26.3.4 signal test) |
| Phase 43 (Resource Limit) | 5 | **DONE** (--max-turns + --max-tokens + dashboard budget + nonlocal fix + 7 tests) |
| Phase 44 (Bug Fix K8-K14) | 7 | **DONE** (1 P0 transitive blocked + 3 P1 + 3 P2, +1 test) |
| Phase 45 (E2E 验证) | 7 | **DONE** (auth + 2-task + 3-task dep chain + conflict resolution + budget enforcement) |
| Phase 46 (用户文档) | 2 | **DONE** (README.md + PLAN.md 精简) |
| Phase 47 (技术债清理) | 7 | **DONE** (git_utils 统一 + json import + 空提交 + commit msg) |
| Phase 48 (设计改进) | 5 | **DONE** (budget 文档 + KeyboardInterrupt + Write sandbox + 2 tests) |
| Phase 49 (Integrator 多策略) | 3 | **DONE** (--strategy flag + merge/rebase 策略 + 5 tests + 代码审查修复) |
| Phase 50 (Watch WebSocket) | 4 | **DONE** (server.py + --web flag + HTML dashboard + 8 tests) |
| Phase 51 (异步 I/O 优化 + 收尾) | 6 | **DONE** (async I/O + D.2 微秒 run_id + 最终审查 6 issues 修复) |
| Phase 29.1 (Docker 沙箱) | 4 | **REMOVED** (v6.0 评估：改为 Dockerfile 提供) |
| Phase 52 (安全加固) | 3 | **DONE** (3/3, api_key 传递 + run.lock + WS Origin 校验, +10 tests) |
| Phase 53 (运行时稳健性) | 5 | **DONE** (5/5, auth 缓存 + asyncio config + graceful shutdown + import 清理 + io lock) |
| Phase 54 (性能优化) | 3 | **DONE** (3/3, Dashboard 增量 + git for-each-ref + 版本号缓存, +7 tests) |
| Phase 55 (代码质量) | 5 | **DONE** (5/5, mypy + 类型标注 + _execute_run 拆分 + 版本号 + Template) |
| Phase 56 (日志与可观测性) | 2 | **DONE** (2/2, 日志截断 + Dockerfile, +4 tests) |
| **Total remaining** | **6** | 6 手动验证 (D.3-D.8) |

---

## v6.0 — Phase 52: 安全加固 (P0, 2026-05-21)

### 52.1 API key 安全传递
- [x] **52.1.1** `cli/run.py` — 移除 `os.environ["ANTHROPIC_API_KEY"] = args.api_key`，改为在 `_execute_run` 中将 key 传递给 dispatcher
- [x] **52.1.2** `dispatcher.py` — `run()` 接受 `api_key: str | None` 参数，传递给 `run_agent()`
- [x] **52.1.3** `agent.py` — `run_agent()` 接受 `api_key: str | None`，仅在 `create_subprocess_exec(env=...)` 中为 claude 子进程注入 `ANTHROPIC_API_KEY`，不污染全局 env
- [x] **52.1.4** `integrator.py` — `_run_claude_agent()` 同样接受并传递 `api_key`
- [x] **52.1.5** 测试：2 个新测试验证 `os.environ` 中无 API key 泄露 + env=None 默认行为

### 52.2 并发运行互斥锁
- [x] **52.2.1** `cli/run.py` — `_run_lock` 上下文管理器，`.cagent/run.lock` 文件锁（Windows: `msvcrt.locking`, Unix: `fcntl.flock`）
- [x] **52.2.2** 锁获取失败时打印清晰错误："Another cagent run is active in this repository. Use --force to override."
- [x] **52.2.3** `--force` flag 跳过锁检查（适用于确认无冲突的场景）
- [x] **52.2.4** 测试：4 个新测试（acquire/release + force skip + dir creation + error message）

### 52.3 WebSocket Origin 校验
- [x] **52.3.1** `server.py` — `_handle_websocket` + `_is_localhost_origin` 检查 `Origin` header，仅允许 `127.0.0.1`/`localhost`/`::1`
- [x] **52.3.2** 非法 Origin 返回 403 Forbidden，不升级 WebSocket
- [x] **52.3.3** 测试：4 个新测试（localhost 通过 + 非 localhost 拒绝 + 空 origin 允许 + 403 集成测试）

---

## v6.0 — Phase 53: 运行时稳健性 (P1, 2026-05-21)

### 53.1 auth 预检缓存
- [x] **53.1.1** `cli/base.py` — `_auth_preflight_check` 成功后写入 `.cagent/auth_ok`（内容：时间戳）
- [x] **53.1.2** `_auth_preflight_check` 开头检查 `.cagent/auth_ok` 是否存在且时间戳 < 5 分钟，是则跳过
- [x] **53.1.3** `cagent run --api-key` 时始终重新验证（key 变化场景）
- [x] **53.1.4** 测试：4 个新测试（缓存命中 + 过期 + force 忽略缓存 + 成功写入）

### 53.2 pytest asyncio warning 修复
- [x] **53.2.1** `pyproject.toml` — `[tool.pytest.ini_options]` 添加 `asyncio_default_fixture_loop_scope = "function"`

### 53.3 `server.py` graceful shutdown
- [x] **53.3.1** `run_dashboard_server` 注册 SIGINT/SIGTERM handler 调用 `server.stop()`
- [x] **53.3.2** Windows 兼容：`signal.signal(signal.SIGINT, ...)` 替代 `loop.add_signal_handler()`
- [x] **53.3.3** 已有 server 测试覆盖（12 tests）

### 53.4 `cli/run.py` 重复 import 清理
- [x] **53.4.1** 移除 `_execute_run` 内 KeyboardInterrupt handler 中的 `from cagent.tasks import dump_state`

### 53.5 `_flush_io` 显式锁保护
- [x] **53.5.1** `progress.py` — `Dashboard.__init__` 新增 `self._io_lock = threading.Lock()`
- [x] **53.5.2** `_flush_io` 和 `_buffer_event` 中 `_event_buffers` / `_dirty_progress` 的读写用 `_io_lock` 保护
- [x] **53.5.3** 已有 progress 测试覆盖（307 tests total）

---

## v6.0 — Phase 54: 性能优化 (P1, 2026-05-22) ✅

### 54.1 Dashboard 增量更新
- [x] **54.1.1** `progress.py` — `_write_dashboard` 只序列化变化的 task，与上次快照 diff 后写入
- [x] **54.1.2** `server.py` — WebSocket 广播改为 diff 格式（`{type: "diff", tasks: {...}}`），仅发送变化的 task
- [x] **54.1.3** `_DASHBOARD_HTML` — JavaScript 客户端适配增量更新协议（`allTasks` 本地状态 + `Object.assign` 合并 diff）
- [x] **54.1.4** 测试：4 个新测试（增量写入 + merge 一致性 + 空 diff 跳过 + 多次写入累积）

### 54.2 `_cmd_branches` 用 `git for-each-ref`
- [x] **54.2.1** `cli/misc.py` — `_cmd_branches` 用 `git for-each-ref --format='%(refname:short)|%(objectname:short)|%(subject)' refs/heads/cagent/` 替代逐分支 `git log`
- [x] **54.2.2** 测试：3 个新测试（无分支 + 多分支列表 + integration 标记）

### 54.3 `build_shared_context` 版本号缓存
- [x] **54.3.1** `memory.py` — `write()` 和 `append()` 时递增 `self._version: int` 计数器
- [x] **54.3.2** `build_shared_context` 缓存 key 改为 `(ids_tuple, self._version)` 取代 per-file mtime stat
- [x] **54.3.3** 测试：3 个新测试（覆盖写缓存失效 + append 缓存失效 + 无 _get_mtime 方法）

---

## v6.0 — Phase 55: 代码质量 (P2, 2026-05-22) ✅

### 55.1 mypy 集成
- [x] **55.1.1** `pyproject.toml` — 添加 `[tool.mypy]` 配置 + `[project.optional-dependencies] dev` 含 mypy
- [x] **55.1.2** 修复 mypy 报告的 16 处类型错误（8 个文件）
- [x] **55.1.3** `mypy cagent/` 0 errors, `--disallow-any-generics` 0 errors

### 55.2 类型标注补全
- [x] **55.2.1** `cli/run.py` — `list` → `list[Any]`，`Callable` → `Callable[..., Any]`
- [x] **55.2.2** 7 个文件 33 处裸泛型补全（`dict` → `dict[str, Any]` 等）
- [x] **55.2.3** `AgentResult.status` 改为 `Literal["done", "failed", "noop"]` 消除 dispatcher 赋值类型错误

### 55.3 `_execute_run` 拆分
- [x] **55.3.1** 提取 `_dispatch_phase(...)` — 包含 dispatcher 调用 + 结果合并 + 计数输出
- [x] **55.3.2** 提取 `_integrate_phase(...)` — 包含 shared memory 写入 + integration 调用
- [x] **55.3.3** 提取 `_summary_phase(...)` — 包含 summary 写入 + worktree 清理 + 最终输出
- [x] **55.3.4** `_execute_run` 简化为三个 phase 的串联调用

### 55.4 版本号统一
- [x] **55.4.1** `pyproject.toml` — `version = "6.0.0"`
- [x] **55.4.2** PLAN.md / CHECKLIST.md 版本引用同步

### 55.5 `_HOOK_SCRIPT` 模板可读性
- [x] **55.5.1** `safety.py` — `_HOOK_SCRIPT` 从 `.format()` 双花括号改为 `string.Template` + `$patterns_json` 占位符
- [x] **55.5.2** 所有现有 safety 测试通过（hook 脚本功能不变）

---

## v6.0 — Phase 56: 日志与可观测性 (P2, 2026-05-22) ✅

### 56.1 日志大小限制
- [x] **56.1.1** `progress.py` — `_truncate_jsonl_if_large` 工具函数，超过 5MB 时保留最后 80% 行
- [x] **56.1.2** `progress.py` — `_do_flush_io` 写入 events jsonl 后自动调用截断
- [x] **56.1.3** `agent.py` — task log 写入后自动调用截断
- [x] **56.1.4** 测试：4 个新测试（未超限不截断 + 超限保留尾部 + 不存在文件 + 至少保留 1 行）

### 56.2 Dockerfile 提供
- [x] **56.2.1** `Dockerfile` — 基于 `python:3.12-slim`，安装 git + Node.js + claude CLI + cagent
- [x] **56.2.2** `.dockerignore` — 排除 `.git`/`.cagent`/`__pycache__` 等
- [x] **56.2.3** `WORKDIR /workspace`，用户 mount 项目目录到此路径

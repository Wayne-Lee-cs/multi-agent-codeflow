# cagent — Implementation Checklist

> v2.1 (Phase 1-24) completed: 153/165 items (92.7%). Details in [ARCHIVE.md](ARCHIVE.md).
> This file tracks only **remaining** and **new** work.

---

## Remaining from v2.1 (12 items, all LOW/deferred)

### Deferred — acceptable for current version
- [ ] **D.1** `integrator.py` — Empty prompt when first task conflicts with base (edge case)
- [ ] **D.2** `cli.py` — run_id timestamp collision at 1-second resolution (extremely unlikely)

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
- [ ] **26.3.4** 测试：cancel 命令向 PID 发信号（需手动验证）

---

## Phase 27: v3.0 — 测试补全 (P2)

### 27.1 agent.py mock 测试
- [ ] **27.1.1** `tests/test_agent.py` — mock subprocess：正常完成 → commit → done
- [ ] **27.1.2** 超时场景：terminate → kill → failed
- [ ] **27.1.3** 非零退出码 → failed + fail_reason 含 stderr
- [ ] **27.1.4** stdin pipe 错误 → failed
- [ ] **27.1.5** git commit 失败 → failed
- [ ] **27.1.6** conventions + shared_context 注入验证

### 27.2 integrator.py mock 测试
- [ ] **27.2.1** `tests/test_integrator.py` — cherry-pick 成功路径
- [ ] **27.2.2** cherry-pick 冲突 → integrator agent 解决 → continue
- [ ] **27.2.3** integrator agent 失败 → abort → 返回 False
- [ ] **27.2.4** squash 模式验证
- [ ] **27.2.5** partial integration（部分 task 失败）

### 27.3 端到端验收
- [ ] **27.3.1** watch TTY 实际验证（手动）
- [ ] **27.3.2** push 拒绝场景验证（手动）
- [ ] **27.3.3** noop + timeout 混合场景（手动）

---

## Phase 28: v3.0 — 功能增强 (P3)

### 28.1 Integrator 多轮验证
- [ ] **28.1.1** `cli.py` — `--post-integrate-cmd "pytest"` flag
- [ ] **28.1.2** `integrator.py` — cherry-pick 完成后执行用户指定命令
- [ ] **28.1.3** 命令失败 → 给 integrator agent 修复 prompt → 第二轮
- [ ] **28.1.4** 最多重试 2 轮，仍失败则标记 integration 为 partial

### 28.2 Integrator 多策略
- [ ] **28.2.1** `cli.py` — `--strategy cherry-pick|merge|rebase` flag（默认 cherry-pick）
- [ ] **28.2.2** `integrator.py` — `_merge_strategy()` / `_rebase_strategy()` 实现
- [ ] **28.2.3** 测试各策略的冲突/无冲突路径

### 28.3 pip install 支持
- [ ] **28.3.1** `pyproject.toml` — 添加 `[project.scripts] cagent = "cagent.cli:main"`
- [ ] **28.3.2** 验证 `pip install -e .` 后 `cagent run --help` 可用
- [ ] **28.3.3** 保持 `python -m cagent` 仍然可用

### 28.4 Watch WebSocket (P4)
- [ ] **28.4.1** `cagent/server.py` — stdlib HTTP + asyncio WebSocket server
- [ ] **28.4.2** Dashboard 变化时推送 JSON 到 WebSocket clients
- [ ] **28.4.3** `cagent watch --web [port]` flag 启动 server
- [ ] **28.4.4** 简单 HTML 前端（可选）

---

## Phase 29: v3.0 — 安全演进 (P4, 长期)

### 29.1 Docker 沙箱
- [ ] **29.1.1** `cagent/sandbox_docker.py` — Docker container 生命周期管理
- [ ] **29.1.2** `--sandbox docker` flag，worker 在容器内运行
- [ ] **29.1.3** Volume mount worktree，网络隔离
- [ ] **29.1.4** Fallback：Docker 不可用时 warning + 退回 hook 模式

### 29.2 Resource Limit
- [ ] **29.2.1** `--max-tokens-per-task N`：单 task token 上限，达到后 terminate
- [ ] **29.2.2** `--max-cost-per-run $N`：基于 token × price 的花费上限
- [ ] **29.2.3** Dashboard 实时显示累计花费

---

## Progress Summary

| Phase | Items | Status |
|-------|-------|--------|
| v2.1 Remaining (D.1-D.8) | 8 | deferred/unverified |
| Phase 25 (Bug 修复) | 4 | **DONE** |
| Phase 26 (可靠性) | 13 | **DONE** (12/13, 1 手动验证) |
| Phase 27 (测试补全) | 14 | TODO |
| Phase 28 (功能增强) | 14 | TODO |
| Phase 29 (安全演进) | 7 | TODO — 长期 |
| **Total remaining** | **60** | |

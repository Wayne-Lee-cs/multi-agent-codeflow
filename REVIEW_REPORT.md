# cagent — Code Review & Evaluation Report

> v2.1 audit complete. Historical findings (all resolved) archived in [ARCHIVE.md](ARCHIVE.md).
> This file tracks only **open issues** and **evaluation scores**.

---

## Current Scores (v3.0, re-evaluated 2026-05-18)

| Dimension | Score (1-5) | Notes |
|-----------|-------------|-------|
| 功能完整度 | 5 | v1 spec + plan + 依赖调度 + 重试 + token 追踪 + cancel |
| 代码质量 | 4.7 | M1-M4 已修复；agent/integrator 缺 mock（Phase 27） |
| 安全性 | 4.5 | sandbox + push 门控扎实；已知间接执行绕过 |
| 可观测性 | 4.8 | Token 追踪 + LinePrinter flush + dashboard token 展示 |
| 跨平台 | 4.5 | Windows GBK 已修复，compat 层完备 |
| 测试覆盖 | 4.6 | 166 pytest + 61 手动；agent/integrator 模块未 mock |
| 文档 | 4.5 | README + PLAN + CHECKLIST + ARCHIVE 完备 |

**Overall: 4.8/5** (Phase 25-26 完成)

---

## Open Issues

### MEDIUM — 6 items (code review 2026-05-18)

| # | Module | Issue | Impact | Fix | Status |
|---|--------|-------|--------|-----|--------|
| M1 | cli.py:216 | `_get_repo_root()` 无 try/except，非 git 仓库暴露 traceback | 用户体验差 | try/except + 友好错误 | **FIXED** |
| M2 | integrator.py:359 | async `_run_git()` 无 timeout，git 挂起时永久阻塞 | 进程泄漏 | `asyncio.wait_for(..., timeout=60)` | **FIXED** |
| M3 | log.py:25 | LinePrinter cancel 时 queue 未 flush，最后几条 DONE/FAIL 丢失 | 输出不完整 | break 前 drain queue | **FIXED** |
| M4 | memory.py:62 | `build_shared_context` 缓存不感知内容变化 | integrator append 后返回过期数据 | per-file mtime 加入 cache key | **FIXED** |
| M5 | agent.py | 无 pytest mock 测试，仅靠 E2E 验证 | 回归风险 | Phase 27 补充 | open |
| M6 | integrator.py | 无 pytest mock 测试，仅靠 E2E 验证 | 回归风险 | Phase 27 补充 | open |

### LOW — 5 items (deferred, non-blocking)

| # | Module | Issue | Status |
|---|--------|-------|--------|
| L1 | integrator.py | 空 prompt — 首个 task 与 base 冲突时 merged_summaries 为空 | Edge case, 不影响功能 |
| L2 | cli.py | run_id 1 秒分辨率，理论碰撞 | 实践中不可能 |
| L3 | safety.py | 间接执行绕过（bash x.sh） | Known limitation, v3 Docker 解决 |
| L4 | agent.py | API Key 在 os.environ 中可见 | CLI 标准做法 |
| L5 | cli.py | architect prompt injection | 用户即作者，无第三方场景 |

### INFO — Unverified scenarios (need manual test)

| # | Scenario | Reason |
|---|----------|--------|
| I1 | `cagent watch` TTY ANSI 刷新 + q 退出 | 需要交互式终端 |
| I2 | `cagent watch` 非 TTY 退化 | 需要 pipe 环境 |
| I3 | `cagent push` 拒绝场景 | 需要远程 repo |
| I4 | `--worker-model` 实际传递 | 需要有效 API key |
| I5 | `--timeout 1` 强制超时 | 需要运行中的 claude |

---

## Test Coverage Matrix

| Module | pytest | Manual | Gap |
|--------|--------|--------|-----|
| tasks.py | 22 | — | ✅ Complete |
| safety.py | 53 | 33 | ✅ Complete |
| progress.py | 40 | 6 | ✅ Complete (+7 token tests) |
| compat.py | 7 | — | ✅ Complete |
| worktree.py | 8 | — | ✅ Complete |
| dispatcher.py | 17 | 2 | ✅ (+4 retry tests) |
| memory.py | 19 | 5 | ✅ Complete |
| **agent.py** | **0** | 2 | ⚠️ Needs mock tests |
| **integrator.py** | **0** | 2 | ⚠️ Needs mock tests |
| **log.py** | **0** | 6 | Can be directly tested |
| cli.py | 0 | 14 | E2E level OK |
| **Total** | **166** | **61** | |

---

## v3.0 Evaluation Targets

### Phase 25 修复后（4 项 bug fix）：

| Dimension | Current | After Phase 25 | Delta |
|-----------|---------|----------------|-------|
| 代码质量 | 4.3 | 4.7 | +0.4 |
| 可观测性 | 4.3 | 4.6 | +0.3 |

### Phase 26-27 完成后（可靠性 + 测试补全）：

| Dimension | Target | Key deliverable |
|-----------|--------|-----------------|
| 可靠性 | 4.8 | ✅ 自动重试 + token 追踪 + cancel |
| 测试覆盖 | 4.8 | agent/integrator mock tests (~30 新用例) |
| 安全性 | 4.7 | resource limits |
| 功能 | 5 | ✅ cancel + retry + multi-strategy |

---

## Benchmark (latest)

| Mode | Time | Tasks | Speedup |
|------|------|-------|---------|
| Single Agent (serial) | 47.7s | 4 | — |
| cagent (j=4 parallel) | 16.7s | 4 | **2.86x** |

Speedup scales with task weight and count. 4 lightweight tasks is near the lower bound.

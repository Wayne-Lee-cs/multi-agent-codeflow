# cagent — Technical Specification (v12.0 评估)

> 第六次全面评估（2026-05-25）。
> 基于 585 tests, 75.59% coverage, mypy 0 errors, 22 source files。

---

## 1. 评估方法论

### 1.1 评估范围

- 全部 22 个 Python 源文件（cagent/ 目录）
- 全部测试套件（tests/ 目录）
- 配置文件（pyproject.toml）
- 文档（PLAN.md, CHECKLIST.md, README.md）

### 1.2 评估工具

| 工具 | 用途 | 结果 |
|------|------|------|
| pytest | 测试执行 | 585 passed, 0 failures |
| pytest-cov | 覆盖率 | 75.59% (threshold: 75%) |
| mypy | 类型检查 | 0 errors, 22 source files |
| 手动代码审查 | Bug/安全/架构 | 见下文 |

---

## 2. 综合评分 8.3/10

| 维度 | 评分 | 变化 | 关键依据 |
|------|------|------|----------|
| 安全性 | 9.0/10 | ↑ 0.5 | v11.0 P0 `_validate_cmd_str` 换行绕过已修复；DENY_PATTERNS 覆盖 ruby/perl；WebSocket 最大连接数限制已添加 |
| 正确性 | 8.5/10 | → | squash 回滚、path traversal 防护、agent_id 校验均已到位 |
| 测试充分性 | 7.0/10 | → | 75.59% 整体覆盖率达标，但 cli/run.py 47% 为明显薄弱环节 |
| 性能 | 8.5/10 | → | 增量序列化 + 版本号缓存 + 流式截断 + 负缓存修复均已实现 |
| 代码质量 | 8.5/10 | ↑ 0.3 | v11.0 完成 `__all__` 导出 + 策略去重 + `_read_frame` 提取 |
| 架构 | 8.0/10 | → | 模块边界清晰；16 处 `subprocess.run` 未迁移 + 2 处 `ensure_future` 待替换 |

### 2.1 评分趋势

| 版本 | 综合评分 | 测试数 | 覆盖率 |
|------|----------|--------|--------|
| v7.0 | — | 342 | 59% |
| v8.0 | — | 460 | 68% |
| v9.0 | — | 576 | 76% |
| v10.0 | 8.2/10 | 576 | 76% |
| v11.0 | 8.17/10 | 585 | 76% |
| **v12.0** | **8.3/10** | **585** | **75.59%** |

---

## 3. 覆盖率详细分析

### 3.1 高覆盖率模块 (≥80%)

| 模块 | 覆盖率 | 行数 | 说明 |
|------|--------|------|------|
| `__init__.py` | 100% | 0 | 空模块 |
| `__main__.py` | 100% | 4 | 入口检查 |
| `worktree.py` | 100% | 14 | worktree 生命周期 |
| `logcmd.py` | 100% | 76 | 日志命令 |
| `memory.py` | 97% | 74 | 2 行未覆盖（条件 import） |
| `tasks.py` | 96% | 151 | 解析器核心 |
| `cli/__init__.py` | 94% | 95 | CLI 入口 |
| `config.py` | 92% | 51 | 配置解析 |
| `progress.py` | 90% | 398 | Dashboard 核心 |
| `cli/misc.py` | 87% | 156 | 杂项命令 |
| `safety.py` | 86% | 110 | 安全沙箱 |
| `git_utils.py` | 85% | 40 | git helper |
| `agent.py` | 84% | 227 | worker 进程 |
| `dispatcher.py` | 84% | 167 | 调度器 |
| `cli/plan.py` | 81% | 129 | plan 命令 |

### 3.2 低覆盖率模块 (< 75%) — 重点关注

#### cli/run.py: 47% (217/407 行未覆盖) — P0

**未覆盖的关键路径：**

| 行号范围 | 函数/路径 | 说明 |
|----------|-----------|------|
| 58-65 | `_run_lock` 锁获取 | Windows msvcrt.locking + Unix fcntl.flock |
| 84-94 | `_run_lock` 过期锁检测 | PID 检查 + 过期清理 |
| 145-178 | `_dispatch_phase` | dispatcher.run 调用 + 结果合并 |
| 194-233 | `_integrate_phase` | integrator.integrate 调用 + memory 写入 |
| 248-269 | `_summary_phase` | summary 写入 + worktree 清理 |
| 286-367 | `_execute_run` | 三阶段串联 + async I/O + 异常处理 |
| 375-390 | `_cmd_run_inner` | 参数解析 → _execute_run 调用 |
| 446-469 | `_cmd_run_inner` 续 | config 加载 + tasks 解析 |
| 500-551 | `_cmd_resume` | state 加载 + pending tasks 过滤 + path traversal 检查 |
| 625-632 | `_get_run_summary` | summary 文件读取 |

**建议测试策略：** Phase 71 已规划 18 个测试用例，需 mock `dispatcher.run`、`integrator.integrate`、`Dashboard` 来覆盖这些路径。

#### server.py: 63% (134/367 行未覆盖) — P1

**未覆盖的关键路径：**

| 行号范围 | 函数/路径 | 说明 |
|----------|-----------|------|
| 301-332 | `_DASHBOARD_HTML` | 内嵌 HTML 模板（静态内容，低优先级） |
| 354-382 | `_handle_connection` HTTP 路由 | 完整 HTTP 请求处理 |
| 461-591 | `_handle_websocket_messages` | WS 帧解码/分片/ping-pong/close |
| 767-789 | `run_dashboard_server` | 服务器启动 + 信号处理 |

**建议测试策略：** Phase 73 已规划 10 个测试用例，重点覆盖 WS 帧处理和连接异常。

#### integrator.py: 68% (116/366 行未覆盖) — P1

**未覆盖的关键路径：**

| 行号范围 | 函数/路径 | 说明 |
|----------|-----------|------|
| 121-155 | `integrate()` squash 路径 | git reset --soft + commit + 回滚 |
| 283-350 | `_resolve_conflicts` | claude agent 冲突解决 + 标记检查 |
| 378-403 | `_post_integrate_validate` | 验证命令 + repair 循环 |
| 543-655 | `_merge_strategy` | merge 冲突 → resolve → integrated |
| 781-872 | `_rebase_strategy` | cherry-pick replay + 冲突解决 |

**建议测试策略：** Phase 72 已规划 13 个测试用例，重点覆盖冲突解决成功路径和 squash 回滚。

---

## 4. 已修复 Bug 验证

以下 Bug 在之前评估中发现，已在对应 Phase 中修复，本次验证确认修复到位：

### 4.1 `_resolve_claude` 负缓存 (Phase 68.2) ✅

- **原问题**: `@lru_cache` 缓存 `shutil.which` 失败结果，之后安装 claude CLI 后仍返回 fallback
- **修复**: 改为模块级 `_claude_path_cache: str | None` 手动缓存，只缓存成功结果
- **验证**: `agent.py:25-38` 代码正确，测试覆盖

### 4.2 `_commit_result` checkout HEAD (Phase 63.1) ✅

- **原问题**: `git checkout HEAD -- .claude/` 在路径不存在时误报 failed
- **修复**: 使用 `_git_op`（非 `_git_op_checked`），非零退出码不触发 failed
- **验证**: 测试 `test_commit_result_checkout_failures_dont_fail` 覆盖

### 4.3 `--force --all` 确认逻辑 (Phase 76.16) ✅

- **原问题**: `--all --force` 无确认直接删除所有数据
- **修复**: `cli/misc.py:60-69` 要求输入 "yes" 确认
- **验证**: 代码逻辑正确，`elif args.all` 分支独立处理

### 4.4 squash 回滚保护 (Phase 70.2) ✅

- **原问题**: `git commit` 失败后无回滚，工作区状态不一致
- **修复**: `integrator.py:144-149` commit 失败时 `git reset --hard base_sha`
- **验证**: 代码路径正确

### 4.5 `_validate_cmd_str` 换行绕过 (Phase 77.1) ✅

- **原问题**: `re.match` 只检查首行，多行命令绕过验证
- **修复**: 前置 `\n\r\t\x00` 检查 + `re.fullmatch`
- **验证**: 3 个测试覆盖（trailing_newline, crlf, tab）

---

## 5. 代码质量问题

### 5.1 `ensure_future` 废弃 API (Phase 76.7, 未修复)

**文件**: `server.py:778,784`
**问题**: `asyncio.ensure_future()` 在 Python 3.10 中标记为 deprecated
**修复**: 替换为 `asyncio.create_task()`
**影响**: 低 — 功能不受影响，仅影响未来兼容性

### 5.2 CLI `subprocess.run` 未统一 (Phase 76.12, 未修复)

**文件**: `cli/base.py`(3 处), `cli/misc.py`(8 处), `cli/plan.py`(1 处), `cli/run.py`(5 处)
**问题**: 17 处直接调用 `subprocess.run` 而非 `git_utils.run_git`
**风险**: 不一致的超时处理和错误报告
**修复**: 迁移到 `git_utils` 封装

### 5.3 宽泛 `except Exception` (16 处)

**文件和行号**:
- `compat.py:74` — 合理（Windows ctypes 回退）
- `dispatcher.py:110,182,199` — 合理（task 级错误隔离）
- `integrator.py:713,785,870` — 合理（策略级错误隔离）
- `progress.py:343` — 合理（事件持久化容错）
- `cli/run.py:230,317,355` — 需审查 — 可能掩盖真实错误
- `safety.py:213` — 合理（import 回退）
- `server.py:36,742` — 合理（连接处理容错）
- `cli/__init__.py:14,25` — 合理（lazy import 回退）

**结论**: 大部分用法合理（错误隔离/回退），`cli/run.py` 中 3 处可考虑收窄异常类型。

---

## 6. 积压分析

### 6.1 v10.0 Phase 71-76 积压 (64/79 未完成)

| 分类 | 未完成 | 优先级 | 预估工作量 |
|------|--------|--------|-----------|
| 测试覆盖 (Phase 71-73) | 41 | P0-P1 | 大 — 41 个测试用例 |
| 文档同步 (Phase 74) | 6 | P2 | 小 |
| HIGH 修复 (Phase 75) | 1 | P0 | 小 — 1 个测试 |
| MEDIUM 修复 (Phase 76) | 16 | P1 | 中 — 16 个修复 |

### 6.2 优先级排序

1. **Phase 71 (cli/run.py 测试)** — 投入产出比最高，47% → 65% 可显著提升整体覆盖率
2. **Phase 73 (server.py 测试)** — 63% → 75% 对整体影响大
3. **Phase 72 (integrator.py 测试)** — 68% → 80% 对冲突解决路径关键
4. **Phase 76 MEDIUM 修复** — 每项独立，可穿插执行
5. **Phase 74 文档同步** — 最后收尾

---

## 7. 架构评价

### 7.1 强项

- **零依赖**: 仅 stdlib，安装简洁
- **跨平台**: Windows/Unix 兼容层（compat.py）覆盖 stdin/ANSI/atomic_write/进程管理
- **安全沙箱**: 多层防御（regex + token + Write 内容扫描 + 白名单校验）
- **可恢复性**: dump_state/load_state 支持 crash recovery + resume
- **增量更新**: Dashboard diff 广播减少 I/O

### 7.2 改进空间

- **CLI git 调用分散**: 17 处 `subprocess.run` 应迁移到 `git_utils`
- **异步信号处理**: 当前信号处理在 sync 上下文，应迁移到 async 上下文
- **Dashboard 类职责**: 事件追踪 + 持久化 + I/O 管理混合在一个类（Dashboard 约 400 行）
- **测试基础设施**: 缺少集成测试框架（除 E2E 外），无 CI pipeline 定义

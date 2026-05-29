# cagent — Technical Specification (v16.0)

> 最新更新（2026-05-27）。
> 基于 784 tests, 88.44% coverage, mypy 0 errors, 22 source files。
> v14.0: 第七次全面评估 + 8 项 bug 修复。v15.0: 7 项性能优化。v16.0: 覆盖率提升 + MEDIUM 修复 + 安全加固。
> v17.0（2026-05-29）: 第八次全面评估，发现 rebase 策略冲突解决 HIGH bug（被 mock 掩盖）等 8 项问题。详见 §11。

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
| pytest | 测试执行 | 784 passed, 0 failures |
| pytest-cov | 覆盖率 | 88.44% (threshold: 78%) |
| mypy | 类型检查 | 0 errors, 22 source files |
| 手动代码审查 | Bug/安全/架构 | 见下文 |
| 性能 Benchmark | 热路径测量 | 见 §8 |

---

## 2. 综合评分 8.7/10

| 维度 | 评分 | 变化 | 关键依据 |
|------|------|------|----------|
| 安全性 | 9.5/10 | ↑ 0.5 | v16.0: `$(...)` 命令注入修复 + Dashboard token 认证 + Windows 文件锁加固 |
| 正确性 | 9.0/10 | → | v16.0: 吞异常全部加日志 + `except Exception` 收窄 |
| 测试充分性 | 9.0/10 | ↑ 1.0 | 784 tests, 88.44% coverage，5 模块从 64-71% → 82-97% |
| 性能 | 9.0/10 | → | v15.0 优化保持 |
| 代码质量 | 9.0/10 | ↑ 0.5 | v16.0: safety.py 移除 inspect.getsource + 静态字符串 + logging 统一 |
| 架构 | 8.5/10 | ↑ 0.5 | v16.0: except 收窄 + lazy imports 已有机制 + Dashboard token 分层 |

### 2.1 评分趋势

| 版本 | 综合评分 | 测试数 | 覆盖率 |
|------|----------|--------|--------|
| v7.0 | — | 342 | 59% |
| v8.0 | — | 460 | 68% |
| v9.0 | — | 576 | 76% |
| v10.0 | 8.2/10 | 576 | 76% |
| v11.0 | 8.17/10 | 585 | 76% |
| v12.0 | 8.3/10 | 585 | 75.59% |
| v13.0 | — | 675 | ~83% |
| v14.0 | 8.4/10 | 700 | ~83% |
| v15.0 | 8.5/10 | 704 | ~83% |
| **v16.0** | **8.7/10** | **784** | **88.44%** |

---

## 3. 覆盖率详细分析

### 3.1 高覆盖率模块 (≥80%)

| 模块 | 覆盖率 | 行数 | 说明 |
|------|--------|------|------|
| `__init__.py` | 100% | 0 | 空模块 |
| `__main__.py` | 100% | 4 | 入口检查 |
| `worktree.py` | 100% | 14 | worktree 生命周期 |
| `integrator/__init__.py` | 100% | — | integrator 包入口 |
| `logcmd.py` | 100% | 76 | 日志命令 |
| `cli/watch.py` | 97% | 160 | v16.0: 68% → 97% |
| `cli/base.py` | 97% | 143 | v16.0: 71% → 97% |
| `memory.py` | 97% | 74 | 2 行未覆盖（条件 import） |
| `tasks.py` | 96% | 151 | 解析器核心 |
| `cli/__init__.py` | 94% | 95 | CLI 入口 |
| `log.py` | 92% | 80 | v16.0: 71% → 92% |
| `config.py` | 92% | 51 | 配置解析 |
| `compat.py` | 90% | 63 | v16.0: 71% → 90% |
| `progress.py` | 90% | 398 | Dashboard 核心 |
| `cli/misc.py` | 87% | 156 | 杂项命令 |
| `safety.py` | 89% | 110 | v16.0: 静态字符串替代 inspect.getsource |
| `git_utils.py` | 85% | 40 | git helper |
| `agent.py` | 84% | 227 | worker 进程 |
| `dispatcher.py` | 84% | 167 | 调度器 |
| `cli/run.py` | 86% | 407 | v12.0: 47% → 86% |
| `cli/plan.py` | 81% | 129 | plan 命令 |
| `server.py` | 82% | 387 | v16.0: 64% → 82% |

### 3.2 覆盖率提升历史

v16.0 后所有模块覆盖率均 ≥ 82%，无低于 75% 的模块。

| 模块 | v12.0 | v15.0 | v16.0 | 变化 |
|------|-------|-------|-------|------|
| `server.py` | 63% | 64% | 82% | +18% |
| `cli/watch.py` | 68% | 68% | 97% | +29% |
| `cli/base.py` | 65% | 71% | 97% | +32% |
| `compat.py` | 68% | 71% | 90% | +22% |
| `log.py` | 71% | 71% | 92% | +21% |
| `cli/run.py` | 47% | 86% | 86% | — |
| `integrator.py` | 68% | 92% | 92% | — |

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

- **异步信号处理**: 当前信号处理在 sync 上下文，应迁移到 async 上下文
- **Dashboard 类职责**: 事件追踪 + 持久化 + I/O 管理混合在一个类（Dashboard 约 400 行）
- **测试基础设施**: 缺少集成测试框架（除 E2E 外），无 CI pipeline 定义

---

## 8. v14.0 Bug 修复 (2026-05-27)

### 8.1 WebSocket `readexactly()` 修复 (P0)

**文件**: `server.py:310-347`
**问题**: `read(n)` 在 TCP 分片下可能返回少于 n 字节的数据，导致帧解析错误
**修复**: 5 处 `read(n)` → `readexactly(n)`，添加 `asyncio.IncompleteReadError` 捕获
**测试**: `test_read_frame_incomplete_read_returns_none` 新增

### 8.2 `_extract_section` 精确匹配 (P1)

**文件**: `tasks.py:_extract_section`
**问题**: `startswith(heading)` 错误匹配 "Conventions Appendix" 为 "Conventions"
**修复**: `startswith` → `==` 精确匹配
**测试**: `test_section_extraction_exact_match` 新增

### 8.3 `memory.py` 原子写入 (P1)

**文件**: `memory.py:write()`
**问题**: `path.write_text()` 非原子，并发读可能读到半写文件
**修复**: 改用 `atomic_write()` (tempfile.mkstemp + os.replace)
**测试**: `TestAtomicWrite` 2 tests 新增

### 8.4 `_maybe_flush_io()` 竞态修复 (P1)

**文件**: `progress.py:_maybe_flush_io()`
**问题**: time check 在锁外，双线程可能同时判断超时并重复 flush
**修复**: time check 移入 `_io_lock` 保护内

---

## 9. v15.0 性能优化 (2026-05-27)

### 9.1 Benchmark 结果

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| prepare_sandbox (cached) | 4.2ms/次 | 1.0ms/次 | **4.2x** |
| WebSocket XOR (1KB x1000) | 38.8ms | 2.1ms | **18x** |
| WebSocket XOR (10KB x100) | 39.3ms | 1.4ms | **28x** |
| Dashboard JSON x1000 | 78.8ms | 19.9ms | **4.0x** |
| Dashboard 文件体积 | 6,091B | 4,510B | **-26%** |
| Event 对象内存 | ~600B+ | 80B | **大幅减少** |

### 9.2 优化详情 (v15.0)

1. **`safety.py`**: `inspect.getsource` 加 `@lru_cache(maxsize=1)`，首次计算后永久缓存。`string.Template` 预编译为 `_HOOK_TEMPLATE`
2. **`memory.py`**: `from cagent.compat import atomic_write` 从函数体移至模块级导入
3. **`server.py`**: WebSocket XOR masking 从逐字节生成器 `bytes(b ^ mask_key[i % 4] ...)` 改为 `int.from_bytes` 整数级批量 XOR
4. **`server.py`**: 静态 HTML 预编码为 `_DASHBOARD_HTML_BYTES`
5. **`progress.py`**: `Event` 和 `TaskProgress` 加 `@dataclass(slots=True)`，消除 `__dict__` 降低内存
6. **`progress.py`**: JSON 序列化统一使用 `separators=(',',':')`，dashboard/event/progress 全部紧凑
7. **`progress.py`**: `EventParser` 不再将完整原始 JSON 对象保存到 `Event.raw`，每事件节省 500B-2KB

---

## 10. v16.0 下一步优化方向

> 基于 v15.0 覆盖率数据（704 tests, 83.29%）和遗留问题分析。

### 10.1 覆盖率缺口分析

当前 5 个模块覆盖率低于 75%，是拉低整体评分的主要因素：

| 模块 | 覆盖率 | 缺失行 | 关键未覆盖路径 |
|------|--------|--------|----------------|
| `server.py` | 64% | 140/387 | WebSocket 帧完整解码流程、HTTP 路由分发、`run_dashboard_server` 启动/信号处理、连接生命周期管理 |
| `cli/watch.py` | 68% | 51/160 | ANSI dashboard 表格渲染、`_watch_dashboard` 轮询循环、非 TTY 退化逻辑 |
| `compat.py` | 71% | 18/63 | Windows ctypes `SetConsoleMode` 分支、条件 import 回退路径 |
| `cli/base.py` | 71% | 42/143 | `_preflight_check` 边缘情况、`_get_repo_root` 错误处理、auth 诊断输出 |
| `log.py` | 71% | 23/80 | `LinePrinter` 取消/空队列路径、ANSI 颜色条件输出 |

**预期收益**: 5 模块 → 80%+ 可将整体覆盖率从 83% 提升至 ~88%，测试充分性评分 8.0 → 8.5+。

### 10.2 遗留 MEDIUM 修复（6 项）

v10.0 Phase 76 仍有 6 项 MEDIUM 未完成：

| 来源 | 问题 | 影响 |
|------|------|------|
| 76.1 | `enable_ansi()` 无返回值 | 调用方无法判断 ANSI 是否可用，Windows 终端可能输出乱码 |
| 76.2 | `run_git_async` Windows 子进程树未杀 | 超时后孤儿 git 子进程残留，占用文件锁 |
| 76.6 | WebSocket close 帧无状态码 | 不符合 RFC 6455 §5.5.1，部分客户端可能异常 |
| 76.10 | `_is_pid_active` PID 复用 | Windows PID 快速回收场景误判锁状态 |
| 76.11 | `--force` 完全跳过锁 | 应仍获取锁但忽略失败，而非完全绕过锁机制 |
| 76.14 | `_cleanup_sandbox` 双重执行 | `atexit.unregister` 后 SIGKILL 场景无兜底清理 |

### 10.3 架构优化方向

| 方向 | 优先级 | 说明 |
|------|--------|------|
| Dashboard 类拆分 | P2 | 400 行单类拆为 EventTracker + DashboardPersister + facade，降低认知负担 |
| 异步信号处理 | P2 | 信号处理迁入 async 上下文，Unix `loop.add_signal_handler` + Windows `signal.signal` |
| CLI 启动 lazy imports | P3 | 子命令按需导入，减少冷启动 import 链 |
| orjson 可选加速 | P3 | dashboard 高频写入场景 5-10x JSON 序列化加速（可选依赖） |
| `except Exception` 收窄 | P3 | `cli/run.py` 3 处宽泛捕获改为具体类型 |
| 统一日志框架 | P3 | 遗留 61.6 — `print(stderr)` → `logging` + `--verbose`/`--quiet` |

### 10.4 预期评分变化

| 维度 | 当前 | Phase 85 后 | Phase 86 后 | Phase 87 后 |
|------|------|-------------|-------------|-------------|
| 安全性 | 9.0 | 9.0 | 9.0 | 9.0 |
| 正确性 | 9.0 | 9.0 | 9.5 | 9.5 |
| 测试充分性 | 8.0 | 8.5 | 8.5 | 9.0 |
| 性能 | 9.0 | 9.0 | 9.0 | 9.5 |
| 代码质量 | 8.5 | 8.5 | 9.0 | 9.0 |
| 架构 | 8.0 | 8.0 | 8.0 | 8.5 |
| **综合** | **8.5** | **8.7** | **8.8** | **9.1** |

---

## 11. 第八次全面评估（v17.0, 2026-05-29）

### 11.1 评估结论

全部 25 个源文件、测试套件、mypy、pytest 复审。784 tests 全部通过，mypy 0 errors，覆盖率 88%。
本轮发现 **1 个被 mock 掩盖的 HIGH 逻辑 bug** + 3 MEDIUM + 4 LOW + 2 优化方向。整体仍属高质量，但暴露出"过度 mock 掩盖真实失败路径"的系统性测试风险。

### 11.2 确认的 Bug

#### 11.2.1 [HIGH] rebase 策略冲突解决用错完成命令 — `integrator/rebase.py:62`

**现象**：`rebase_strategy` 内部用 `git cherry-pick` 重放提交（`rebase.py:55`），但冲突时调用 `_resolve_conflicts(..., completion_mode="rebase")`。该 `completion_mode` 在 `base.py:362-367` 执行 `git rebase --continue`、在 `base.py:213`（`_abort_operation`）执行 `git rebase --abort`。

**根因**：当前进行中的操作是 cherry-pick，没有 rebase 在进行。真实环境下：
1. `git rebase --continue` → "No rebase in progress" → 抛 RuntimeError
2. except → `git rebase --abort` → 同样失败
3. 返回 False，**cherry-pick 状态悬挂未清理**，污染下一个任务的 cherry-pick（级联失败）

**为什么 784 测试没抓到**：`test_integrate_rebase_strategy_conflict_resolution`（`test_integrator.py:547-563`）的 mock 把 `cherry-pick --continue/--abort` 特判返回 0，而 `rebase --continue` 落到默认分支也返回 0——mock 让一个真实会失败的命令"成功"，测试通过但真实路径是坏的。

**修复**：`completion_mode="rebase"` → `completion_mode="cherry-pick"`（同时修正 continue 与 abort）。与 README "Known Limitations" 中"rebase 内部用 cherry-pick"的声明一致。

**防回归**：补一个不 mock `--continue`/`--abort` 的真实 git repo 集成测试（`tmp_path` 建真实仓库制造 rebase 策略冲突）。

### 11.3 中优先级问题

| # | 文件 | 问题 | 影响 |
|---|------|------|------|
| 11.3.1 | `progress.py:145` | `EventParser._parse_user` 中 `result_content[0].get("text",...)` 未校验元素类型 | 若 `tool_result` content 列表元素是字符串，抛 `AttributeError`；该异常在 `agent.py:188` 流解析循环中仅 TimeoutError 被捕获，冒泡到 dispatcher 把整个任务标记 "unhandled error"——单行畸形输出可搞挂整个任务 |
| 11.3.2 | `config.py:88-114` | `apply_config` 用 `current == default` 判断是否被用户改过 | 用户显式传入恰等于默认值的参数（`--strategy cherry-pick`、`--jobs 4`、`--quiet`）会被配置文件覆盖，违反"CLI 优先于配置"承诺 |
| 11.3.3 | `integrator/merge.py:48,82` | `branch -f`/`branch -D` 作用于仍被 worker worktree 检出的分支 | Git 拒绝操作其他 worktree 检出的分支，命令必失败被 `check=False` 吞掉。无实际危害但属误导性死代码 |

### 11.4 低优先级 / 文档

| # | 位置 | 问题 |
|---|------|------|
| 11.4.1 | `README.md` | 版本/测试数严重过时（`v12.0.0 / 585 tests / 75.59%` vs 实际 `v17.0 / 784 / 88.44%`）；Module Map 仍把 `integrator.py` 当单文件（实为 `integrator/` 包） |
| 11.4.2 | `tests/test_compat.py` | `run_dashboard_server` 协程创建后未 await，产生 `RuntimeWarning: coroutine never awaited` |
| 11.4.3 | `dispatcher.py:193` | `--max-tokens` 预算检查被 `and dashboard` 门控，无 dashboard 时静默失效（隐式耦合） |
| 11.4.4 | `integrator/base.py:320` | `_resolve_conflicts` 的 `git grep` 标记检查：grep 自身异常（rc 128）被当作"无标记"放行（边缘鲁棒性） |

### 11.5 优化方向（非 bug）

| 方向 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| `_check_tokens` 双份维护 | P2 | ✅ 已实施 | 采用一致性测试方案（尊重 v16.0 S4 移除 inspect.getsource 的决策，不回退）。`TestCheckTokensStaticConsistency` exec 嵌入版，对 50 条命令电池断言与运行时逐条一致，锁死漂移 |
| 测试保真度（降低过度 mock） | P1 | ✅ 已实施 | rebase HIGH bug 系被过度 mock 掩盖。新增 `TestStrategiesRealGitConflict`：三策略各一真实 git 冲突解决测试（仅 mock 集成 agent），共享 `_setup_real_conflict_repo` helper，便于为新策略扩展 |
| 大文件拆分 + 覆盖率洼地 | P3 | ⏳ 保留 | `server.py`(851，内嵌 HTML/JS) 可外置；覆盖率重点 `server.py` 81%、`cli/plan.py` 80% |

### 11.6（优化后复审，2026-05-29）

- 测试：**792 passed**（原 784 + 8 新增：1 config 回归 + 3 解析鲁棒 + 1 safety 一致性 + 3 策略真实 git），`-W error::RuntimeWarning` 零警告
- 类型：mypy **0 errors（26 files）**；覆盖率 **88%**（阈值 78%）
- 本次优化为**纯测试新增，生产代码零改动**，无回归面
- 待办：88.O2（server HTML 外置）；可选 — `integrate()` 的策略 if/elif 可演进为注册表式分发以提升可扩展性（当前 3 策略下清晰度足够，暂记备选）

### 11.6 预期评分变化（Phase 88 后）

| 维度 | 当前(v16) | Phase 88 后 | 关键依据 |
|------|-----------|-------------|----------|
| 安全性 | 9.5 | 9.5 | 无新安全问题 |
| 正确性 | 9.0 | 9.5 | 修复 rebase HIGH bug + 解析器鲁棒性 |
| 测试充分性 | 9.0 | 9.2 | 补真实 git 集成测试，减少过度 mock |
| 性能 | 9.0 | 9.0 | → |
| 代码质量 | 9.0 | 9.2 | 清理死代码 + 消除 _check_tokens 双份隐患 |
| 架构 | 8.5 | 8.5 | → |
| **综合** | **8.7** | **9.0** | rebase 真实可用 + 测试可信度提升 |

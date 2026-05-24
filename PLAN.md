# Code Architecture Plan — v10.0 (2026-05-23)

> Phase 1-70 completed. 576 pytest pass, 0 failures. Historical details in [ARCHIVE.md](ARCHIVE.md).

## Current Status

**v6.0 已发布** — 安全加固 + 运行时稳健性 + 性能优化 + 代码质量 + 可观测性，342 自动化测试覆盖。
**v7.0 大部分完成** — 全面评估发现 19 个新问题。Phase 57-62 大部分完成。407 tests, 65% coverage, mypy 0 errors。
**v8.0 已发布** — Phase 63-65 完成。460 tests, 68% coverage, mypy 0 errors, 0 RuntimeWarning。
**v9.0 完成** — Phase 66-70 全部完成。576 tests, 76% coverage, mypy 0 errors。Bug修复+安全加固+性能优化+测试覆盖提升+代码审查修复。
**v10.0 进行中** — 第四次全面评估。综合评分 8.2/10。重点：测试覆盖缺口修复（cli/run.py 49%, integrator.py 66%, server.py 64%）+ 文档同步。
**v11.0 评估完成** — 第五次全面评估（2026-05-24）。综合评分 8.17/10。发现 S1 `_validate_cmd_str` 换行绕过（P0 安全漏洞）等 15 项新问题。Phase 77-79 规划。

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

---

## v9.0 Roadmap

### Phase 66: Bug 修复 (P0) 🔴

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 66.1 | `integrator._run_claude_agent` stdin 超时保护 | **高** | `integrator.py:249-253` — `drain()/wait_closed()` 无超时，可能永久挂起。与 `agent.py:158-164` 对齐，添加 30s/5s 超时 |
| 66.2 | `integrator._run_claude_agent` FileNotFoundError 处理 | **高** | `integrator.py:236` — `create_subprocess_exec` 未捕获 `FileNotFoundError`/`OSError`。claude 不在 PATH 时 traceback 不友好。返回 None（调用方已处理） |
| 66.3 | `RunMemory` agent_id 路径遍历验证 | 中 | `memory.py:28,33,42` — `write/append/read` 直接拼接 agent_id 到路径，无 `../`/`/` 检查。添加 `_validate_agent_id()` 校验 |
| 66.4 | `_extract_prompt` 误过滤 prompt 中的 field 行 | 中 | `tasks.py:200` — prompt 含 `- **word**: value` 格式行时被错误跳过。改为仅在 heading 后连续 field 区检查 |

### Phase 67: 安全加固 (P1) 🟡

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 67.1 | CORS preflight 无 Origin 返回 `*` | 中 | `server.py:401-405,579` — 无 Origin 的 OPTIONS 请求返回 `Allow-Origin: *`。改为无 Origin 时不设置 CORS 头 |
| 67.2 | `_validate_cmd_str` 允许反引号 | 低 | `integrator.py:168` — 白名单含 `` ` ``，bash 中触发命令替换。移除反引号 |

### Phase 68: 性能优化 (P2) 🟢

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 68.1 | Dashboard 增量序列化 | 低 | `progress.py:494-515` — `_write_dashboard` 改为只序列化 `_dirty_progress` 中的 task，从 O(N) 降至 O(dirty) |
| 68.2 | `_resolve_claude` 负缓存修复 | 低 | `agent.py:24` — `@lru_cache` 缓存失败结果。改为只缓存正值 |
| 68.3 | `_truncate_jsonl_if_large` 流式处理 | 低 | `progress.py:195-212` — 5MB 文件全量读入。改为尾部 seek 查找截断点 |

### Phase 69: 代码质量与测试 (P2) 🟢

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 69.1 | rebase 策略命名修正 | 低 | `integrator.py:791-879` — 内部用 cherry-pick 实现，文档/注释注明 "replay" 语义 |
| 69.2 | 测试覆盖率 68% → 75% | 低 | 重点: `cli/__init__.py` 15%→50%, `cli/run.py` 49%→65%, `server.py` 53%→70% |
| 69.3 | EventParser 非 JSON 行优化 | 低 | `progress.py:56-63` — 合并连续非 JSON 行，减少 Event 对象分配 |
| 69.4 | 遗留 Phase 58-62 未完成项收尾 | 低 | 58.2(README api-key), 60.6(fail_under), 61.6(统一日志) |

### Phase 70: 代码审查修复 (P2-P3) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 70.1 | Dashboard commit SHA 同步 | 中 | `integrator.py:621` — `_resolve_conflicts` 更新 `task.commit_sha` 后需同步 `dashboard.set_task_status` |
| 70.2 | squash 回滚保护 | 中 | `integrator.py:144-149` — `git commit` 失败时 `git reset --hard` 恢复 |
| 70.3 | `_validate_task_id` 加固 | 低 | `progress.py:230` — 改为 `^[a-zA-Z0-9_-]+$`，拒绝 Windows 非法文件名字符 |
| 70.4 | branch name 显式传参 | 低 | `integrator.py:739` — 移除 `split('/')[1]` 硬编码，`_merge_strategy` 接受 `run_id` 参数 |
| 70.5 | 空 prompt commit message | 低 | `agent.py:341` — `first_line` 为空时 fallback `"(no description)"` |
| 70.6 | conflict prompt 大小限制 | 低 | `integrator.py:489` — `merged_summaries` 超过 2000 字符截断 |
| 70.7 | sandbox 清理泛化 | 低 | `integrator.py:573` — `shutil.rmtree(.claude/)` 替代硬编码文件列表 |

### 遗留手动验证 (P3, 不阻塞发布)

| # | 验证项 |
|---|--------|
| D.3 | `cagent watch` TTY 下 1s 刷新表格 + `q` 退出 |
| D.4 | `cagent watch` 非 TTY 下退化为单次 status |
| D.5 | `cagent push` 输入 `n` / 回车 / Ctrl-C → 无 push 发生 |
| D.6 | `--worker-model claude-haiku-4-5` 时 worker 命令行含 `--model` |
| D.7 | 不可执行任务 → 标 noop，integrator 跳过 |
| D.8 | `--timeout 1` → 标 failed，integrator 合入成功部分 |

---

## v10.0 Roadmap

> 第四次全面评估（2026-05-23）。综合评分 8.2/10，测试充分性 7.5/10 为主要短板。
> 详见 [SPEC_v10.md](SPEC_v10.md)。

### Phase 71: 测试覆盖提升 — cli/run.py (P0) 🔴

**目标**: cli/run.py 覆盖率 49% → 65%+

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 71.1 | `_dispatch_phase` mock 测试 | 中 | mock dispatcher.run，验证结果合并 + 计数输出 + budget 超限 |
| 71.2 | `_integrate_phase` mock 测试 | 中 | mock integrator.integrate，验证 memory 写入 + 跳过 + 失败 |
| 71.3 | `_summary_phase` mock 测试 | 低 | mock dashboard.flush + worktree 清理 + summary 输出 |
| 71.4 | `_execute_run` 完整路径 mock 测试 | 中 | 三阶段串联 + KeyboardInterrupt + 异常处理 |
| 71.5 | `_cmd_run_inner` 完整 run 路径 | 中 | mock _execute_run，验证参数传递 + config 加载 |
| 71.6 | `_cmd_resume` 实际执行路径 | 中 | mock load_state + _execute_run，验证 base_sha fallback |

### Phase 72: 测试覆盖提升 — integrator.py (P1) 🟡

**目标**: integrator.py 覆盖率 66% → 80%+

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 72.1 | `_resolve_conflicts` 完整成功路径 | 中 | mock agent 返回 0 + grep 无残留 → done + dashboard 同步 |
| 72.2 | `_resolve_conflicts` 冲突标记残留 | 低 | mock grep 发现残留 → abort_operation |
| 72.3 | `_post_integrate_validate` repair 成功 | 中 | mock 第一轮失败 + agent 修复 + 第二轮成功 → True |
| 72.4 | `_merge_strategy` 冲突解决成功 | 中 | mock merge 冲突 → resolve → integrated |
| 72.5 | `_rebase_strategy` 冲突解决成功 | 中 | mock cherry-pick 冲突 → resolve → integrated |
| 72.6 | squash commit 失败回滚 | 低 | mock commit 失败 → reset --hard base_sha |
| 72.7 | `_rebase_strategy` run_id 显式传参 | 低 | 移除 `split('/')[1]` 硬编码，新增 run_id 参数 |

### Phase 73: 测试覆盖提升 — server.py (P1) 🟡

**目标**: server.py 覆盖率 64% → 75%+

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 73.1 | WebSocket 多帧拼接解码 | 低 | 构造 multi-frame payload 验证正确拼接 |
| 73.2 | WebSocket ping/pong 处理 | 低 | 发送 ping 帧验证自动 pong 响应 |
| 73.3 | 连接异常断开清理 | 低 | mock ConnectionResetError → 资源清理 |
| 73.4 | HTTP 非 GET 方法处理 | 低 | POST/PUT → 405 Method Not Allowed |
| 73.5 | 边界情况（超大帧、空帧） | 低 | 超过 _MAX_WS_FRAME_SIZE → 关闭连接 |

### Phase 74: 收尾与文档同步 (P2) 🟢

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 74.1 | README.md 版本号同步 | 低 | v6.0.0 → v9.0.0，更新测试数和覆盖率 |
| 74.2 | pyproject.toml fail_under 提升 | 低 | 75 → 78，与实际覆盖率匹配 |
| 74.3 | 全量验证 | 低 | mypy 0 errors + 576+ tests + coverage ≥ 78% |
| 74.4 | PLAN/CHECKLIST 状态同步 | 低 | 更新文档至最新状态 |

---

## v10.0 Roadmap — 代码审查修复 (2026-05-23 并行审查)

> cagent 并行审查发现 42 个新问题。详见 [REVIEW_REPORT.md](REVIEW_REPORT.md)。

### Phase 75: 代码审查修复 — HIGH (P0) 🔴

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 75.1 | git_utils 异常类型统一 | 高 | `run_git` 抛 `RuntimeError`，`run_git_async` 抛 `GitTimeoutError`，统一为 `GitTimeoutError` |
| 75.2 | `_HOOK_SCRIPT` 模板安全 | 高 | `.replace()` 二次替换可能互相干扰，改用 `string.Template` |
| 75.3 | `_is_localhost_origin` scheme 校验 | 高 | 未校验 scheme，`file://localhost` 可绕过 |
| 75.4 | `_run_lock` 过期锁检测 | 高 | 进程 kill -9 后锁文件残留，获取锁前检查 PID 活跃性 |

### Phase 76: 代码审查修复 — MEDIUM (P1) 🟡

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 76.1 | `enable_ansi()` 返回值 | 中 | ctypes 调用未检查返回值 |
| 76.2 | `run_git_async` Windows 子进程清理 | 中 | `proc.kill()` 不杀子进程 |
| 76.3 | `_validate_agent_id` null byte | 中 | 缺少 `\x00` 检查 |
| 76.4 | `memory.py` OSError 处理 | 中 | `write()`/`append()`/`read()` 未处理磁盘错误 |
| 76.5 | `DENY_PATTERNS` 绝对路径 | 中 | `/usr/bin/git push` 可绕过 |
| 76.6 | close 帧状态码 | 中 | RFC 6455 不合规 |
| 76.7 | `ensure_future` → `create_task` | 中 | Python 3.10+ 废弃 API |
| 76.8 | API key 诊断泄露 | 中 | `_print_auth_diagnostics` 暴露前 8 + 后 4 字符 |
| 76.9 | `auth_ok` 并发写入 | 中 | 使用 `atomic_write` 替代 `write_text` |
| 76.10 | `_is_pid_active` PID 复用 | 中 | Windows 上 PID 复用可能误判 |
| 76.11 | `_run_lock` force 模式 | 中 | `--force` 完全跳过锁 |
| 76.12 | CLI git 操作统一 | 中 | 11 处直接调用 `subprocess.run` |
| 76.13 | symlink 循环保护 | 中 | `_scan_dir_tree` 无限递归风险 |
| 76.14 | `_cleanup_sandbox` 双重执行 | 中 | atexit 被 unregister 后无兜底 |
| 76.15 | `_follow_file` 文件消失检测 | 中 | 删除后无限空读循环 |
| 76.16 | `_cmd_clean --all --force` 确认 | 中 | 误操作永久删除所有记录 |

### 未覆盖的审查范围

| 范围 | 原因 |
|------|------|
| dispatcher.py + integrator.py | cagent task-004 stream 解析失败 |
| test infrastructure | cagent task-006 stream 解析失败 |

---

## v11.0 Roadmap

> 第五次全面评估（2026-05-24）。综合评分 8.17/10。
> 新发现 15 项问题（6 安全, 5 Bug, 2 性能, 2 架构）。
> 详见 [SPEC_v10.md](SPEC_v10.md) v11.0 评估章节。

### Phase 77: 安全修复 (P0) 🔴

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 77.1 | `_validate_cmd_str` 换行符绕过修复 | **高** | `integrator.py:166-177` — `re.match` 只检查首行。改用 `re.fullmatch` + 拒绝 `\n`/`\r`/`\t` |
| 77.2 | 换行绕过测试 | 低 | 多行命令字符串被拒绝的测试用例 (3+ tests) |

### Phase 78: 安全+健壮性 (P1) 🟡

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 78.1 | WebSocket 最大连接数限制 | 中 | `server.py` — `self.connections` 无上限，添加 `_MAX_CONNECTIONS` (默认 50) |
| 78.2 | `args.resume` 路径遍历防护 | 中 | `cli/run.py:486` — `resolve()` 后校验在 `.cagent/runs/` 下 |
| 78.3 | `_broadcast` 并行发送 | 中 | `server.py:730-745` — 串行 `await conn.send()` 改为 `asyncio.gather()` |
| 78.4 | DENY_PATTERNS 补充 | 低 | `safety.py` — 添加 `ruby -e`/`perl -e` 模式 |
| 78.5 | S3 `env_continue` 冗余构建 | 低 | `integrator.py:564` — 移除内部 `_ensure_no_claude_dir` 中的冗余 env 构建 |
| 78.6 | S6 `_broadcast` 错误连接未移除 | 低 | `server.py` — 发送失败的连接未从 `self.connections` 移除 |

### Phase 79: 代码质量 (P2) 🟢

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 79.1 | `ref_content` 截断提示 | 低 | `cli/plan.py` — `ref_content[:4000]` 截断时打印 warning |
| 79.2 | `_summary_phase` memory_dir 异常保护 | 低 | `cli/run.py` — `iterdir()` 加 `try/except PermissionError` |
| 79.3 | Dashboard 加载 kind 值验证 | 低 | `progress.py` — 加载时过滤无效 `kind` 值 |
| 79.4 | `_read_frame` 提取为方法 | 低 | `server.py:498-531` — 从循环内闭包提取为 `WebSocketConnection._read_frame()` |
| 79.5 | integrator 策略代码去重 | 中 | `integrator.py:667-905` — 三策略共享代码提取公共模板 |
| 79.6 | `__all__` 导出控制 | 低 | 核心模块添加 `__all__` 声明 |
| 79.7 | B2 `_rebase_strategy` run_id 参数化 | 低 | 已知 BUG-1（Phase 72.7），换行传参修复 |
| 79.8 | B5 `_watch_dashboard` 轮询间隔可配置 | 低 | `server.py:689` — 硬编码 1s 改为可配置 |

---

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

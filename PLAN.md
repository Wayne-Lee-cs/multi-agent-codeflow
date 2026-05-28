# Code Architecture Plan — v10.0 (2026-05-23)

> Phase 1-70 completed. 576 pytest pass, 0 failures. Historical details in [ARCHIVE.md](ARCHIVE.md).

## Current Status

**v6.0 已发布** — 安全加固 + 运行时稳健性 + 性能优化 + 代码质量 + 可观测性，342 自动化测试覆盖。
**v7.0 大部分完成** — 全面评估发现 19 个新问题。Phase 57-62 大部分完成。407 tests, 65% coverage, mypy 0 errors。
**v8.0 已发布** — Phase 63-65 完成。460 tests, 68% coverage, mypy 0 errors, 0 RuntimeWarning。
**v9.0 完成** — Phase 66-70 全部完成。576 tests, 76% coverage, mypy 0 errors。Bug修复+安全加固+性能优化+测试覆盖提升+代码审查修复。
**v10.0 进行中** — 第四次全面评估。综合评分 8.2/10。重点：测试覆盖缺口修复（cli/run.py 49%, integrator.py 66%, server.py 64%）+ 文档同步。
**v11.0 评估完成** — 第五次全面评估（2026-05-24）。综合评分 8.17/10。发现 S1 `_validate_cmd_str` 换行绕过（P0 安全漏洞）等 15 项新问题。Phase 77-79 规划。
**v12.0 评估完成** — 第六次全面评估（2026-05-25）。综合评分 8.3/10。无新 P0 漏洞。主要短板：v10.0 积压 64/79 未完成（cli/run.py 47% 覆盖率为首要目标）。Phase 80-82 规划。
**v13.0 已发布** — 5 项安全与架构修复。675 tests, 92% integrator coverage。
**v14.0 已发布** — 第七次全面评估 + 8 项安全与 bug 修复。WebSocket readexactly、_extract_section 精确匹配、memory atomic_write、I/O throttle 竞态修复。700 tests, 83% coverage。
**v15.0 已发布** — 性能优化：7 项改动使项目更轻量更快速。XOR masking 18x、prepare_sandbox 4.2x、JSON 序列化 4x、内存占用显著减少。704 tests。
**v16.0 已发布** — 覆盖率提升（5 模块 → 80%+）+ 遗留 MEDIUM 修复（6 项）+ 架构深度优化 + 5 项安全修复。784 tests, 88.44% coverage。Phase 85-87 + 安全修复全部完成。
**v17.0 进行中** — 第八次评估（2026-05-28）。7 项审查 fix + 1 项 fixture 加固已合入 PR #1（800 tests）。剩余：Phase 88 测试脆弱性收敛（7 项）+ Phase 89 优化方向（5 项）。

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

## v12.0 Roadmap

> 第六次全面评估（2026-05-25）。综合评分 8.3/10。
> 585 tests, 75.59% coverage, mypy 0 errors, 22 source files。
> pyproject.toml 版本 9.0.0。
> 结论：项目质量稳定，无新 P0 安全漏洞。主要短板为 v10.0 Phase 71-76 积压（64/79 未完成）。
> 详见 [SPEC.md](SPEC.md)。

### 评估总结

| 维度 | 评分 | 说明 |
|------|------|------|
| 安全性 | 9.0 | v11.0 P0 换行绕过已修复，DENY_PATTERNS 完善，沙箱覆盖全面 |
| 正确性 | 8.5 | 主路径全覆盖，边缘情况（squash 回滚、path traversal）已加固 |
| 测试充分性 | 7.0 | 75.59% 覆盖率达标，但 cli/run.py 47%、server.py 63% 为短板 |
| 性能 | 8.5 | 增量序列化、版本号缓存、流式截断已实现 |
| 代码质量 | 8.5 | mypy 0 errors, __all__ 导出, 策略去重完成 |
| 架构 | 8.0 | 模块边界清晰，但 16 处 subprocess.run + 2 处 ensure_future 待统一 |

### 覆盖率分布

| 模块 | 覆盖率 | 缺失行 | 优先级 |
|------|--------|--------|--------|
| cli/run.py | 47% | 217/407 | **P0** — Phase 71 |
| server.py | 63% | 134/367 | P1 — Phase 73 |
| cli/base.py | 65% | 53/152 | P1 |
| integrator.py | 68% | 116/366 | P1 — Phase 72 |
| compat.py | 68% | 13/41 | P2 |
| cli/watch.py | 68% | 51/160 | P2 |
| log.py | 71% | 23/80 | P2 |

### Phase 80: 版本号同步 + 积压清理 (P0) 🔴

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 80.1 | pyproject.toml 版本号同步 | 低 | `9.0.0` → `12.0.0`，与 PLAN.md 对齐 |
| 80.2 | Phase 71 执行 — cli/run.py 测试 | 中 | 47% → 65%+，18 个测试用例，覆盖 _dispatch/_integrate/_summary/_execute_run |
| 80.3 | Phase 72 执行 — integrator.py 测试 | 中 | 68% → 80%+，冲突解决/repair/squash 回滚路径 |
| 80.4 | Phase 73 执行 — server.py 测试 | 中 | 63% → 75%+，WS 帧拼接/ping-pong/连接清理 |

### Phase 81: v10.0 Phase 76 MEDIUM 收尾 (P1) 🟡

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 81.1 | 76.3 `_validate_agent_id` null byte | 中 | memory.py — 添加 `\x00` 检查 |
| 81.2 | 76.4 memory.py OSError 处理 | 中 | write/append/read 添加 try/except OSError |
| 81.3 | 76.7 ensure_future → create_task | 低 | server.py:778,784 — Python 3.10+ 废弃 API |
| 81.4 | 76.9 auth_ok 并发写入 | 低 | cli/base.py — 使用 atomic_write |
| 81.5 | 76.12 CLI git 操作统一 | 中 | 18 处 subprocess.run → git_utils 封装 |
| 81.6 | 76.15 _follow_file 文件消失检测 | 低 | cli/logcmd.py — 连续空读超限后退出 |

### Phase 82: 文档同步 + 验证 (P2) 🟢

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 82.1 | README.md 版本号同步 | 低 | v6.0.0 → v12.0.0，更新测试数+覆盖率 |
| 82.2 | pyproject.toml fail_under 提升 | 低 | 75 → 78，Phase 80 完成后调整 |
| 82.3 | 全量验证 | 低 | mypy 0 + 600+ tests + coverage ≥ 78% |
| 82.4 | PLAN/CHECKLIST 状态同步 | 低 | 标记所有已完成项 |

### v10.0 积压状态

| Phase | 已完成 | 总计 | 状态 |
|-------|--------|------|------|
| 71 (cli/run.py 测试) | 0 | 18 | TODO |
| 72 (integrator.py 测试) | 0 | 13 | TODO |
| 73 (server.py 测试) | 0 | 10 | TODO |
| 74 (收尾文档) | 0 | 6 | TODO |
| 75 (HIGH 修复) | 9 | 10 | 1 待做 |
| 76 (MEDIUM 修复) | 6 | 22 | 16 待做 |

---

## v14.0 Roadmap

> 第七次全面评估（2026-05-27）+ bug 修复。700 tests, 83% coverage。

### Phase 83: Bug 修复 (P0) ✅

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| ~~83.1~~ | ~~WebSocket `read()` → `readexactly()` 修复~~ | **高** | `server.py` — 5 处 `read(n)` 在 TCP 分片下可能返回不完整数据。改用 `readexactly(n)` + `IncompleteReadError` 捕获 |
| ~~83.2~~ | ~~`_extract_section` 前缀匹配 bug~~ | 中 | `tasks.py` — `startswith(heading)` 错误匹配"Conventions Appendix"。改用 `== target` 精确匹配 |
| ~~83.3~~ | ~~`memory.py` `write()` 非原子写入~~ | 中 | `memory.py` — `path.write_text()` 改为 `atomic_write()` 避免并发读到半写文件 |
| ~~83.4~~ | ~~`_maybe_flush_io()` 竞态条件~~ | 中 | `progress.py` — time check 移入 `_io_lock` 保护内，避免双线程同时判断超时 |

---

## v15.0 Roadmap

> 性能优化（2026-05-27）。使项目更轻量更快速。704 tests。

### Phase 84: 性能优化 (P1) ✅

| # | 任务 | 提升 | 说明 |
|---|------|------|------|
| ~~84.1~~ | ~~`inspect.getsource` 缓存~~ | 4.2x | `safety.py` — `_get_check_tokens_source()` 加 `@lru_cache`，首次后无 inspect 开销 |
| ~~84.2~~ | ~~`string.Template` 预编译~~ | 微量 | `safety.py` — `_HOOK_TEMPLATE` 预编译避免每次重新解析模板 |
| ~~84.3~~ | ~~`atomic_write` 导入提升~~ | 微量 | `memory.py` — `from cagent.compat import atomic_write` 从函数内移至模块级 |
| ~~84.4~~ | ~~WebSocket XOR `int.from_bytes` 优化~~ | 18-28x | `server.py` — 逐字节生成器替换为整数级 XOR 批量运算 |
| ~~84.5~~ | ~~静态 HTML 预编码~~ | 微量 | `server.py` — `_DASHBOARD_HTML_BYTES` 预编码避免每次请求 `.encode()` |
| ~~84.6~~ | ~~`__slots__` 数据类优化~~ | 内存减少 | `progress.py` — `Event`/`TaskProgress` 加 `slots=True`，消除 `__dict__` 开销 |
| ~~84.7~~ | ~~紧凑 JSON 序列化~~ | 4x | `progress.py` — dashboard/event/progress 输出用 `separators=(',',':')`，体积-26% |
| ~~84.8~~ | ~~`Event.raw` 不存储完整 JSON~~ | 内存减少 | `progress.py` — EventParser 不再将完整原始对象保存到 `Event.raw`，每事件节省 500B-2KB |

### 性能 Benchmark 对比

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| prepare_sandbox (cached) | 4.2ms | 1.0ms | 4.2x |
| WebSocket XOR 1KB x1000 | 38.8ms | 2.1ms | 18x |
| WebSocket XOR 10KB x100 | 39.3ms | 1.4ms | 28x |
| Dashboard JSON x1000 | 78.8ms | 19.9ms | 4.0x |
| Dashboard 文件体积 (20 tasks) | 6,091B | 4,510B | -26% |
| Event 对象内存 | ~600B+ | 80B | 大幅减少 |

---

## v16.0 Roadmap — 下一步优化方向

> 基于 v15.0 覆盖率数据（704 tests, 83.29%）和遗留问题分析。
> 重点：低覆盖模块提升 + 遗留 MEDIUM 修复 + 架构深度优化。

### Phase 85: 覆盖率提升 — 低覆盖模块 (P1) ✅

**目标**: 5 个低覆盖模块 → 80%+，整体 83% → 88%+

| # | 模块 | 之前 | 之后 | 说明 |
|---|------|------|------|------|
| ~~85.1~~ | `server.py` | 64% | 82% | 30+ 新测试：HTTP 路由、WebSocket 帧、连接管理 |
| ~~85.2~~ | `cli/watch.py` | 68% | 97% | TTY 循环、ANSI 颜色、web 模式测试 |
| ~~85.3~~ | `cli/base.py` | 71% | 97% | auth 诊断、find_run_dir、terminate_pid 测试 |
| ~~85.4~~ | `compat.py` | 71% | 90% | Windows ctypes mock、stdin key、atomic_write 测试 |
| ~~85.5~~ | `log.py` | 71% | 92% | verbose done/error/denied、quiet done 测试 |

### Phase 86: 遗留 MEDIUM 修复收尾 (P1) ✅

**目标**: 清理 v10.0 Phase 76 剩余 6 项 MEDIUM 问题

| # | 任务 | 来源 | 说明 |
|---|------|------|------|
| ~~86.1~~ | `enable_ansi()` 返回值 | 76.1 | `compat.py` — 返回 `bool`，Windows `SetConsoleMode` 结果检查 |
| ~~86.2~~ | `run_git_async` Windows 子进程清理 | 76.2 | `git_utils.py` — `CREATE_NEW_PROCESS_GROUP` + `CTRL_BREAK_EVENT` 杀进程树 |
| ~~86.3~~ | WebSocket close 帧状态码 | 76.6 | `server.py` — close 帧含 `\x03\xe8` (RFC 6455 §5.5.1 状态码 1000) |
| ~~86.4~~ | `_is_pid_active` PID 复用防护 | 76.10 | `cli/run.py` — lock 文件含 `PID:TIMESTAMP`，24h 过期检测 |
| ~~86.5~~ | `_run_lock` force 模式完善 | 76.11 | `cli/run.py` — `--force` 仍获取锁但忽略失败（打印警告） |
| ~~86.6~~ | `_cleanup_sandbox` 双重执行兜底 | 76.14 | `cli/plan.py` — `_cleanup_done` 幂等保护 |

### Phase 87: 架构与性能深度优化 (P2) 部分完成

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| ~~87.4~~ | `except Exception` 收窄 | ✅ | `cli/run.py` + `cli/__init__.py` — 收窄为 `(RuntimeError, OSError, ValueError)` |
| 87.1 | Dashboard 类拆分 | TODO | `progress.py` — 拆为 EventTracker + DashboardPersister |
| 87.2 | 异步信号处理迁移 | TODO | `cli/run.py` — Unix/Windows 信号处理 |
| 87.3 | CLI 启动 lazy imports | TODO | `cli/__init__.py` — 已有 `__getattr__` 机制 |
| 87.5 | `pyproject.toml fail_under` 提升 | TODO | 78 → 85 |
| 87.6 | orjson 可选加速 | TODO | `progress.py` — 可选依赖 |
| 87.7 | 统一日志框架 | TODO | 遗留 61.6 |

### 安全修复 (v16.0 额外) ✅

> 代码审查发现的 5 项安全漏洞/弱点，全部修复。

| # | 优先级 | 任务 | 说明 |
|---|--------|------|------|
| S1 | P0 | `_validate_cmd_str` 增加 `$(...)` 检测 | `integrator/base.py` — 阻止命令替换注入 |
| S2 | P1 | 吞异常处加 `logging.warning()` | `memory.py`(6处) + `progress.py`(3处) + `server.py`(4处) |
| S3 | P1 | Windows 文件锁改为锁整个文件大小 | `cli/run.py` — `msvcrt.locking` 从 1 字节改为 payload 长度 |
| S4 | P2 | safety.py 静态字符串替代 `inspect.getsource` | `safety.py` — `_CHECK_TOKENS_STATIC` 静态 fallback，移除 `functools` 依赖 |
| S5 | P2 | Dashboard 加 token 认证 | `server.py` — HTTP/WebSocket 请求需 `?token=...`，防止未授权访问 |

### v16.0 优先级排序

1. ~~**Phase 85.1 (server.py 覆盖率)**~~ — ✅ 64% → 82%
2. ~~**Phase 86.1-86.3 (核心 MEDIUM 修复)**~~ — ✅ enable_ansi/进程树/RFC 合规
3. ~~**Phase 85.2-85.5 (其余覆盖率)**~~ — ✅ 4 模块 → 90-97%
4. **Phase 87.1-87.2 (架构优化)** — TODO — Dashboard 拆分 + 异步信号
5. **Phase 87.3-87.7 (锦上添花)** — TODO — 性能微调 + 代码质量

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

---

## v17.0 Roadmap (2026-05-28)

第八次全面评估。已修复 7 项审查发现（已合入 PR #1）。剩余工作聚焦于**测试脆弱性收敛**与若干优化方向。

### 已完成（已合入 PR #1, commit `19eed96`）

| # | 严重度 | 位置 | 修法 |
|---|--------|------|------|
| R1 | 中 | `agent.py:_commit_result` | 调换 `git add -A` 与 `.gitignore` 还原顺序；新建 .gitignore 用 `rm --cached --ignore-unmatch` 兜底。运行期 `__pycache__`/`.venv`/`.env`/`node_modules` 不再被提交 |
| R2 | 中 | `safety.py` | 为部署到 hook 的静态副本 `_CHECK_TOKENS_STATIC` 与活函数 `_check_tokens` 加 18 例参数化一致性测试 |
| R3 | 低 | `worktree.py:cleanup_orphan_worktrees` | 真正删除孤儿 `cagent/<run>/*` 分支（docstring 之前声称但未实现） |
| R4 | 低 | `tasks.py:parse_tasks_md` | 检测重复 `### Task NNN` 并报错 |
| R5 | 低 | `config.py:_parse_and_validate` | int 配置键显式拒绝 TOML 布尔值（bool 是 int 子类陷阱） |
| R6 | 低 | `dispatcher.py` | 移除波次调度死代码 `failed` 集合 |
| R7 | 低 | `integrator/base.py:_resolve_conflicts` | 用定点删除替代 `shutil.rmtree(.claude)`，保留用户被追踪的 `.claude/` 内容 |
| F1 | 附加 | `tests/conftest.py:tmp_repo` | 显式 `git config commit.gpgsign=false`，让 worktree/e2e 测试不依赖宿主签名配置（修复了 19 个环境性 ERROR） |

### Phase 88: 测试脆弱性收敛 (P1) — TODO

> 全套测试本地仍有 7 项失败，全部为**测试环境脆弱性**（非产品 bug）。需固化平台/输入条件，让套件在任意环境确定性通过。

| # | 测试 | 失败原因 | 修法 |
|---|------|----------|------|
| 88.1 | `test_compat::TestStdinHasKey::test_returns_truthy` | pytest 捕获 stdin 下 `sys.stdin.fileno()` 抛 `UnsupportedOperation` | mock `sys.stdin` 为有 `fileno()` 的对象，或用 `-s` 标记 + `monkeypatch` |
| 88.2 | `test_compat::TestStdinHasKey::test_windows_uses_kbhit` | Linux 环境无 `msvcrt.kbhit` | `@pytest.mark.skipif(sys.platform != "win32")` 或 mock 整个 `msvcrt` 模块（`sys.modules["msvcrt"] = MagicMock()`） |
| 88.3 | `test_compat::TestReadKey::test_windows_uses_getwch` | 同 88.2，无 `msvcrt.getwch` | 同上 |
| 88.4 | `test_compat::TestEnableAnsi::test_windows_calls_set_console_mode` | 无 `ctypes.windll` | mock `ctypes.windll` 为 MagicMock + skipif |
| 88.5 | `test_cli::TestTerminatePidWindows::test_terminate_windows_taskkill_fallback` | `subprocess.run` mock 路径不全 | 完整 mock `subprocess.run` 的 Windows 分支 + 平台检测 patch |
| 88.6 | `test_cli::TestTerminatePidWindows::test_terminate_windows_taskkill_also_fails` | 同 88.5 | 同上 |
| 88.7 | `test_cli::TestTerminatePidWindows::test_terminate_windows_oserror_fallback` | 同 88.5 | 同上 |

**判定标准**：在 Linux 环境下 `python -m pytest -q` 0 失败 0 错误；CI 中 Windows runner 仍能跑被 skip 的平台测试。

### Phase 89: 优化方向（来自第八次审查） — TODO

| # | 优先级 | 任务 | 位置 | 说明 |
|---|--------|------|------|------|
| 89.1 | P2 | Token 预算解耦 dashboard | `dispatcher.py:193` | 当前 `if max_tokens is not None and dashboard` — 无 dashboard 时预算完全不生效。改为独立 token 计数器，dashboard 仅作展示 |
| 89.2 | P2 | 安全检查单一源生成 | `safety.py` | 构建期由活函数源生成 hook 副本，根除维护两份的负担（R2 的参数化测试只能捕获、不能预防） |
| 89.3 | P3 | 中间轮 token 统计 | `progress.py:403` | 目前只累计 `result` 事件 usage，多轮对话中间 assistant 的 usage 未计入，可能低估预算消耗 |
| 89.4 | P3 | 根目录文档归档 | 根目录 | 13 个 md（SPEC_v9/SPEC_v10/REVIEW/REVIEW_REPORT/CHECKLIST 85KB 等），版本化文档统一归 `ARCHIVE.md` 或 `docs/` |
| 89.5 | P3 | `run_git_async` 超时后 `proc.wait()` 加超时 | `git_utils.py:117` | Windows 路径 `CTRL_BREAK_EVENT` 后无界等待，进程不响应会挂死 |

### v17.0 优先级排序

1. **Phase 88.1-88.7** — 测试脆弱性收敛（P1，本地 0 失败的硬性目标）
2. **Phase 89.1** — Token 预算解耦（P2，正确性影响）
3. **Phase 89.2** — 安全检查单一源（P2，维护性）
4. **Phase 89.3-89.5** — 锦上添花（P3）

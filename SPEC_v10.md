# cagent v10.0 — 全面评估与改进规格说明

> 第四次全面评估（2026-05-23）。576 tests 全部通过，76.08% coverage，mypy 0 errors，0 RuntimeWarning。
> 基于架构、代码质量、安全、测试、可维护性六维度系统评估。

---

## 评估总览

### 量化指标

| 指标 | 数据 |
|------|------|
| 源码模块 | 22 个 Python 文件，5,066 行 |
| 测试文件 | 22 个，7,013 行 |
| 测试/源码比 | 1.38:1 |
| 测试用例 | 576 个，全部通过 |
| 覆盖率 | 76.08%（阈值 75%） |
| mypy | 0 错误 |
| 运行时依赖 | 0（纯 stdlib） |
| 测试运行时间 | 49.37s |

### 六维评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 9/10 | 清晰分层、零依赖、职责单一 |
| 代码质量 | 8/10 | 类型完备、防御编程、原子操作 |
| 安全性 | 8/10 | 多层防御、已知限制记录清晰 |
| 测试充分性 | 7.5/10 | 量够但核心路径覆盖不足 |
| 可维护性 | 8.5/10 | 模块化好、文档完善 |
| 跨平台支持 | 8/10 | Windows/Unix 双路径覆盖 |
| **综合** | **8.2/10** | **生产就绪，测试缺口需补** |

---

## 覆盖率分析

### 各模块覆盖率

| 模块 | 行数 | 覆盖率 | 评级 | 说明 |
|------|------|--------|------|------|
| memory.py | 68 | 100% | 完美 | |
| tasks.py | 146 | 97% | 完美 | |
| safety.py | 80 | 96% | 优秀 | |
| cli/logcmd.py | 76 | 100% | 完美 | |
| config.py | 48 | 94% | 优秀 | |
| cli/__init__.py | 96 | 93% | 优秀 | |
| progress.py | 394 | 90% | 优秀 | |
| cli/misc.py | 150 | 87% | 良好 | |
| agent.py | 226 | 84% | 良好 | |
| dispatcher.py | 166 | 84% | 良好 | |
| cli/plan.py | 125 | 82% | 良好 | |
| git_utils.py | 39 | 77% | 良好 | |
| log.py | 80 | 71% | 一般 | |
| compat.py | 41 | 68% | 一般 | |
| cli/watch.py | 160 | 68% | 一般 | |
| **integrator.py** | **369** | **66%** | **不足** | 冲突解决路径测试缺失 |
| cli/base.py | 152 | 65% | 一般 | |
| **server.py** | **350** | **64%** | **不足** | WebSocket 帧处理未覆盖 |
| worktree.py | 14 | 0%* | N/A | 仅包装 git 命令 |
| **cli/run.py** | **381** | **49%** | **严重不足** | 核心执行路径缺覆盖 |

*worktree.py 通过集成测试间接覆盖。

### 覆盖率缺口根因

#### cli/run.py (49%) — 最大缺口

缺失覆盖的关键路径：
- `_execute_run()`: 主执行流程（dispatch → integrate → summary）
- `_dispatch_phase()`: dispatcher 调用 + 结果合并
- `_integrate_phase()`: shared memory 写入 + integration 调用
- `_summary_phase()`: summary 写入 + worktree 清理
- `_cmd_run_inner()` 的完整 run 路径（非 dry-run）
- `_cmd_resume()` 的实际 resume 执行路径

**根因**: 这些函数涉及多个子系统（dispatcher、integrator、worktree）的交互，mock 复杂度高。当前测试只覆盖了 dry-run 和错误路径。

#### integrator.py (66%) — 冲突解决缺口

缺失覆盖的路径：
- `_resolve_conflicts()` 的完整成功路径（agent 解决冲突 → 无残留标记 → 完成操作）
- `_post_integrate_validate()` 的 repair agent 修复成功路径
- `_merge_strategy()` 冲突解决后的集成流程
- `_rebase_strategy()` 冲突解决后的集成流程
- squash 路径的 `git commit` 失败回滚

**根因**: 冲突解决需要模拟 claude agent 的输出和 git 状态，mock 链路长。

#### server.py (64%) — WebSocket 缺口

缺失覆盖的路径：
- WebSocket 帧的完整编解码（mask/unmask、多帧拼接、ping/pong）
- 并发 WebSocket 连接管理
- 连接异常断开的清理逻辑
- HTTP 请求的完整路由处理

**根因**: WebSocket 协议实现复杂，当前测试主要覆盖 HTTP 和基础连接。

---

## Bug 清单

### BUG-1: `_rebase_strategy` 仍使用字符串解析获取 run_id (P3)

**位置**: `integrator.py:837`
**现状**: `integration_branch.split("/")[1]` 通过字符串解析获取 run_id。Phase 70.4 已修复 `_merge_strategy`（显式传入 `run_id` 参数），但 `_rebase_strategy` 遗漏。
**修复**: `_rebase_strategy` 新增 `run_id` 参数，与 `_merge_strategy` 一致。

### BUG-2: README 版本号过时 (P3)

**位置**: `README.md:3`
**现状**: `> **v6.0.0** — 326 tests, zero third-party dependencies.`
**实际**: v9.0.0, 576 tests, 76% coverage。
**修复**: 更新版本号、测试数和覆盖率数据。

---

## 优化清单

### OPT-1: cli/run.py 测试覆盖提升 (P0)

**目标**: 49% → 65%+

**策略**: 使用 mock 隔离外部依赖（dispatcher.run、integrator.integrate、worktree），测试核心控制流。

**需覆盖的函数**:

| 函数 | 当前状态 | 目标 | 测试策略 |
|------|----------|------|----------|
| `_execute_run` | 未覆盖 | 核心路径 | mock dispatcher + integrator，验证三阶段串联 |
| `_dispatch_phase` | 未覆盖 | 成功+失败 | mock dispatcher.run，验证结果合并和计数 |
| `_integrate_phase` | 未覆盖 | 成功+跳过+失败 | mock integrator.integrate，验证 memory 写入 |
| `_summary_phase` | 未覆盖 | 成功路径 | mock dashboard.flush，验证 summary 输出 |
| `_cmd_run_inner` | 部分覆盖 | 完整 run | mock _execute_run，验证参数传递 |
| `_cmd_resume` | 部分覆盖 | 实际执行 | mock load_state + _execute_run |

**预估新增**: 15-20 个测试用例。

### OPT-2: integrator.py 测试覆盖提升 (P1)

**目标**: 66% → 80%+

**策略**: mock `_run_claude_agent` 和 `_run_git`，测试各策略的冲突解决路径。

**需覆盖的函数**:

| 函数 | 当前状态 | 目标 | 测试策略 |
|------|----------|------|----------|
| `_resolve_conflicts` | 部分覆盖 | 完整成功路径 | mock agent 返回 0 + 无残留标记 |
| `_resolve_conflicts` | 未覆盖 | 冲突标记残留 | mock grep 发现残留 → abort |
| `_post_integrate_validate` | 部分覆盖 | repair 成功 | mock 第一轮失败 + agent 修复 + 第二轮成功 |
| `_merge_strategy` | 部分覆盖 | 冲突解决成功 | mock merge 冲突 → resolve → 成功 |
| `_rebase_strategy` | 部分覆盖 | 冲突解决成功 | mock cherry-pick 冲突 → resolve → 成功 |
| squash 路径 | 未覆盖 | commit 失败回滚 | mock commit 失败 → reset --hard |

**预估新增**: 12-15 个测试用例。

### OPT-3: server.py 测试覆盖提升 (P1)

**目标**: 64% → 75%+

**策略**: 测试 WebSocket 帧编解码的边界情况和 HTTP 路由。

**需覆盖的路径**:

| 路径 | 当前状态 | 目标 | 测试策略 |
|------|----------|------|----------|
| WS 帧解码：多帧拼接 | 未覆盖 | 覆盖 | 构造 multi-frame payload |
| WS 帧解码：mask 处理 | 部分覆盖 | 完整 | 测试 masked/unmasked 帧 |
| WS ping/pong | 未覆盖 | 覆盖 | 发送 ping 帧验证 pong 响应 |
| 连接异常断开 | 未覆盖 | 覆盖 | mock ConnectionResetError |
| HTTP 非 GET 方法 | 未覆盖 | 覆盖 | POST/PUT → 405 |

**预估新增**: 15-20 个测试用例。

### OPT-4: 综合测试运行验证 (P2)

**目标**: 确保所有新增测试通过，覆盖率 ≥ 78%。

**验收标准**:
- `python -m pytest tests/ -v` 0 failures
- `python -m pytest tests/ --cov=cagent --cov-report=term-missing` ≥ 78%
- `python -m mypy cagent/` 0 errors
- `fail_under` 从 75 提升到 78

---

## 安全评估

### 已实现的安全措施（20+ 项）

| 层级 | 措施 | 状态 |
|------|------|------|
| 命令沙箱 | 20+ deny patterns（git push/rm -rf/bash -c/python -c/node -e/powershell 等） | ✅ |
| 命令沙箱 | shlex token 化检测 split-flag 模式（rm -i -r -f） | ✅ |
| 内容扫描 | Write/Edit 工具内容检查（defense-in-depth） | ✅ |
| 网络安全 | WebSocket Origin 校验（仅 localhost） | ✅ |
| 网络安全 | HTTP 安全头（nosniff + CSP） | ✅ |
| 网络安全 | CORS preflight 校验（无 Origin 不返回 Allow-Origin） | ✅ |
| 密钥安全 | API key 不污染 os.environ，仅注入子进程 env | ✅ |
| 文件安全 | atomic_write 防临时文件竞争（mkstemp） | ✅ |
| 路径安全 | agent_id path traversal 验证 | ✅ |
| 路径安全 | task_id 非法字符过滤（仅允许 a-zA-Z0-9_-） | ✅ |
| 命令校验 | post_integrate_cmd 字符白名单（已移除反引号） | ✅ |
| XSS 防护 | Dashboard HTML 使用 textContent 替代 innerHTML | ✅ |
| 并发安全 | run.lock 文件锁（Windows msvcrt / Unix fcntl） | ✅ |
| 资源限制 | --max-tokens 预算控制 + --max-turns 限制 | ✅ |

### 已知限制（已记录，不阻塞发布）

| 风险 | 说明 | 缓解措施 |
|------|------|----------|
| `--api-key` 进程参数泄露 | 值出现在 ps aux / wmic 中 | README 标注推荐 ANTHROPIC_API_KEY 环境变量 |
| 间接执行绕过 | 编译二进制/非 Bash 解释器可绕过 hook | Known Limitations 记录，建议 Docker 容器运行 |
| Write 内容误报 | 测试/文档中提及危险命令可能被阻止 | defense-in-depth 权衡，已记录 |
| Budget overshoot | 并发 task 间 --max-tokens 检查非原子 | help 文档已说明 |

---

## 架构亮点

### 设计优点
1. **零依赖**: 纯 Python 3.11+ stdlib，无供应链风险
2. **职责分离**: agent/dispatcher/integrator/progress/safety 各司其职
3. **git_utils 统一**: 所有 git 操作统一超时和错误处理
4. **增量 I/O**: Dashboard 只序列化脏 task，async I/O worker 不阻塞主循环
5. **Wave-based 调度**: 依赖图自动排序 + 有界并发 + 指数退避重试
6. **跨平台**: Windows（msvcrt/CREATE_NEW_PROCESS_GROUP）和 Unix（fcntl/SIGTERM）全覆盖

### 模块依赖图

```
cli/ (入口)
 ├── run.py → dispatcher.py → agent.py → git_utils.py
 │                            → worktree.py → git_utils.py
 │           → integrator.py → agent.py (claude subprocess)
 │                           → git_utils.py
 │                           → safety.py
 │           → progress.py → compat.py
 │           → memory.py
 ├── watch.py → progress.py
 ├── plan.py → safety.py
 ├── logcmd.py → progress.py
 └── misc.py → git_utils.py

server.py → progress.py (独立 HTTP/WS server)
config.py (独立配置加载)
log.py → progress.py (控制台输出)
```

---

## Phase 规划

### Phase 71: 测试覆盖提升 — cli/run.py (P0)

| # | 任务 | 预估测试数 |
|---|------|-----------|
| 71.1 | `_dispatch_phase` mock 测试（成功 + 失败 + budget） | 4 |
| 71.2 | `_integrate_phase` mock 测试（成功 + 跳过 + 失败） | 3 |
| 71.3 | `_summary_phase` mock 测试 | 2 |
| 71.4 | `_execute_run` 完整路径 mock 测试 | 3 |
| 71.5 | `_cmd_run_inner` 完整 run 路径 | 3 |
| 71.6 | `_cmd_resume` 实际执行路径 | 3 |

### Phase 72: 测试覆盖提升 — integrator.py (P1)

| # | 任务 | 预估测试数 |
|---|------|-----------|
| 72.1 | `_resolve_conflicts` 完整成功路径 | 2 |
| 72.2 | `_resolve_conflicts` 冲突标记残留 → abort | 1 |
| 72.3 | `_post_integrate_validate` repair 成功路径 | 2 |
| 72.4 | `_merge_strategy` 冲突解决成功 | 2 |
| 72.5 | `_rebase_strategy` 冲突解决成功 | 2 |
| 72.6 | squash commit 失败回滚 | 1 |
| 72.7 | `_rebase_strategy` run_id 显式传参 | 1 |

### Phase 73: 测试覆盖提升 — server.py (P1)

| # | 任务 | 预估测试数 |
|---|------|-----------|
| 73.1 | WebSocket 多帧拼接解码 | 2 |
| 73.2 | WebSocket ping/pong 处理 | 2 |
| 73.3 | 连接异常断开清理 | 2 |
| 73.4 | HTTP 非 GET 方法处理 | 2 |
| 73.5 | 边界情况（超大帧、空帧） | 3 |

### Phase 74: 收尾与文档同步 (P2)

| # | 任务 |
|---|------|
| 74.1 | README.md 版本号 v6.0.0 → v9.0.0 |
| 74.2 | pyproject.toml `fail_under` 75 → 78 |
| 74.3 | 验证 mypy 0 errors + 全部测试通过 |
| 74.4 | PLAN.md / CHECKLIST.md 状态同步 |

---

# cagent v11.0 — 第五次全面评估与改进规格说明

> 第五次全面评估（2026-05-24）。578 tests 全部通过，75.35% coverage，mypy 0 errors。
> 基于架构、代码质量、安全、测试、可维护性六维度系统评估。对所有源码逐行审查。

---

## 评估总览

### 量化指标

| 指标 | v10.0 数据 | v11.0 数据 | 变化 |
|------|-----------|-----------|------|
| 源码模块 | 22 个 Python 文件 | 22 个 Python 文件 | = |
| 源码语句 | 5,066 行 | 3,246 语句 | — |
| 测试用例 | 576 个 | 578 个 | +2 |
| 覆盖率 | 76.08% | 75.35% | -0.73% |
| mypy | 0 错误 | 0 错误 | = |
| 运行时依赖 | 0 | 0 | = |
| 测试运行时间 | 49.37s | 55.24s | +5.87s |

### 六维评分

| 维度 | v10.0 | v11.0 | 变化 | 说明 |
|------|-------|-------|------|------|
| 架构设计 | 9/10 | 9/10 | = | 清晰分层、零依赖、职责单一 |
| 代码质量 | 8/10 | 8/10 | = | 类型完备、防御编程、原子操作 |
| 安全性 | 8/10 | 7.5/10 | ↓0.5 | S1 `_validate_cmd_str` 换行符绕过为新发现高危项 |
| 测试充分性 | 7.5/10 | 7/10 | ↓0.5 | 覆盖率微降，cli/run.py 47% 仍未改善 |
| 可维护性 | 8.5/10 | 8/10 | ↓0.5 | integrator 三策略重复代码增多 |
| 跨平台支持 | 8/10 | 8/10 | = | Windows/Unix 双路径覆盖 |
| **综合** | **8.2/10** | **7.9/10** | **↓0.3** | **S1 需立即修复** |

---

## 新发现问题清单

### 安全漏洞（6 项）

#### S1 — `_validate_cmd_str` 换行符绕过 [P0/HIGH] 🔴

**文件**: `integrator.py:166-177`
**问题**: 正则 `r'^[\w .\-\/\\:=+,@~()\[\]{}|&;!?\*#$%^\'"<>]+$'` 使用 `re.match`（非 `re.fullmatch`），`re.match` 仅匹配从字符串开头到第一个 `\n` 的部分。如果 `cmd_str` 包含换行符，第二行完全不受检查。
**攻击向量**: `cmd_str = "echo ok\nrm -rf /"` → `re.match` 只检查 `echo ok`，通过验证。
**修复**: 在函数开头添加 `if '\n' in cmd_str or '\r' in cmd_str or '\t' in cmd_str: return False`。

#### S2 — WebSocket 连接无速率限制 [P1/MEDIUM]

**文件**: `server.py:297-310`
**问题**: `DashboardServer.connections` 是无限增长的列表。同机恶意进程可打开大量 WebSocket 连接导致内存耗尽。
**修复**: 添加 `_MAX_CONNECTIONS = 50` 上限，超过时拒绝新连接并返回 503。

#### S3 — `_resolve_conflicts` 中 `env_continue` 冗余 `os.environ` 拷贝 [P2/LOW]

**文件**: `integrator.py:592`
**问题**: `{**os.environ, "GIT_EDITOR": "true"}` 每次调用 `_resolve_conflicts` 都拷贝完整 env dict。虽然 Phase 64.5 标记为 FIXED，但拷贝仍在发生。
**影响**: 性能微损，非安全问题。

#### S4 — safety sandbox 不拦截 `ruby -e` / `perl -e` [P2/LOW]

**文件**: `safety.py:32-63`
**问题**: DENY_PATTERNS 覆盖了 python/node/powershell/cmd/deno/bash/sh，但遗漏 `ruby -e` 和 `perl -e`。
**修复**: 添加 `r"\bruby\s+-e\b"` 和 `r"\bperl\s+-e\b"` 到 DENY_PATTERNS。

#### S5 — `_cmd_plan` 中 `ref_content` 静默截断 [P2/LOW]

**文件**: `cli/plan.py:112`
**问题**: `ref_content[:4000]` 静默截断参考文件，大文件用户不知道只有前 4000 字符被传入。
**修复**: 截断时打印 warning。

#### S6 — `_validate_cmd_str` 不拒绝 tab 字符 [P2/LOW]

**文件**: `integrator.py:166-177`
**问题**: `\t` 不在白名单字符中但也不被拒绝（因为 `re.match` + `$` 行为）。Tab 可用于命令混淆。
**修复**: 与 S1 一并修复。

### Bug 和正确性问题（5 项）

#### B1 — `args.resume` 路径遍历风险 [P1/MEDIUM]

**文件**: `cli/run.py:486`
**问题**: `args.resume` 直接用于 `runs_dir / args.resume`，用户传入 `../../etc` 可能导致路径遍历（仅读取 tasks.json，非执行）。
**修复**: 验证 `args.resume` 仅包含 `[a-zA-Z0-9_\-T.]`。

#### B2 — `_rebase_strategy` 仍通过字符串解析获取 run_id [P1/MEDIUM]

**文件**: `integrator.py:839`
**问题**: `run_id = integration_branch.split("/")[1]` 依赖分支命名格式。已在 BUG-1（SPEC_v10.md）中记录，Phase 72.7 规划但未实施。
**状态**: 已知，待 Phase 72.7 修复。

#### B3 — Dashboard 加载时 `kind` 值不验证 [P2/LOW]

**文件**: `progress.py:271-278`
**问题**: 从 `dashboard.json` 恢复 `last_event` 时 `kind=v.get("kind", "text")` 不验证合法性。
**修复**: 添加 `if kind not in Event.__dataclass_fields__[...]` 检查（或使用 fallback）。

#### B4 — `_summary_phase` 中 `memory_dir.iterdir()` 可能抛异常 [P2/LOW]

**文件**: `cli/run.py:263`
**问题**: Windows 上若 memory 目录被其他进程锁定，`any(memory_dir.iterdir())` 抛 PermissionError，中断 summary。
**修复**: 加 `try/except (OSError, PermissionError)`。

#### B5 — `_watch_dashboard` 轮询间隔硬编码 [P2/LOW]

**文件**: `server.py:689-728`
**问题**: 固定 1 秒轮询，高频更新时客户端延迟较高。
**建议**: 可配置或使用 `asyncio.Event` 通知机制。

### 性能问题（2 项）

#### P1 — `_broadcast` 串行发送 [P1/MEDIUM]

**文件**: `server.py:730-745`
**问题**: 对每个 WebSocket 连接串行 `await conn.send(message)`。慢连接阻塞所有客户端。
**修复**: 使用 `asyncio.gather()` 并行发送。

#### P2 — `_read_frame` 每次循环重新创建闭包 [P2/LOW]

**文件**: `server.py:498-531`
**问题**: `async def _read_frame()` 定义在 while 循环内部，每次迭代创建新闭包对象。
**修复**: 提取为 `WebSocketConnection._read_frame()` 方法。

### 架构和可维护性问题（2 项）

#### A1 — integrator.py 三策略代码重复度高 [P2/MEDIUM]

**文件**: `integrator.py:667-905`
**问题**: `_cherry_pick_strategy`、`_merge_strategy`、`_rebase_strategy` 共享大量样板逻辑。908 行可精简至 ~650 行。
**建议**: 提取公共循环+错误处理模式。

#### A2 — 缺少 `__all__` 导出控制 [P2/LOW]

**问题**: 所有公共模块没有 `__all__`，`from cagent.xxx import *` 导入内部实现。
**修复**: 核心模块添加 `__all__`。

---

## 覆盖率分析（v11.0 更新）

### 各模块覆盖率

| 模块 | 语句 | 覆盖率 | 评级 | 与 v10.0 对比 |
|------|------|--------|------|--------------|
| memory.py | 73 | 97% | 优秀 | -3% (新增 validate) |
| tasks.py | 150 | 96% | 优秀 | -1% |
| cli/logcmd.py | 76 | 100% | 完美 | = |
| config.py | 51 | 92% | 优秀 | -2% |
| cli/__init__.py | 95 | 94% | 优秀 | +1% |
| progress.py | 394 | 90% | 优秀 | = |
| cli/misc.py | 156 | 87% | 良好 | = |
| safety.py | 110 | 86% | 良好 | -10% (新增 tokens) |
| git_utils.py | 39 | 85% | 良好 | +8% |
| agent.py | 226 | 84% | 良好 | = |
| dispatcher.py | 166 | 84% | 良好 | = |
| cli/plan.py | 127 | 82% | 良好 | = |
| log.py | 80 | 71% | 一般 | = |
| compat.py | 41 | 68% | 一般 | = |
| cli/watch.py | 160 | 68% | 一般 | = |
| **integrator.py** | **372** | **66%** | **不足** | = |
| cli/base.py | 152 | 65% | 一般 | = |
| **server.py** | **360** | **63%** | **不足** | -1% |
| **cli/run.py** | **400** | **47%** | **严重不足** | -2% |

### 覆盖率缺口优先级

| 优先级 | 模块 | 覆盖率 | 缺失关键路径 |
|--------|------|--------|-------------|
| **P0** | cli/run.py | 47% | `_execute_run` 完整路径、`_cmd_run_inner`、`_cmd_resume` 执行路径 |
| **P1** | server.py | 63% | WebSocket 帧收发、并发连接管理、ping/pong |
| **P1** | integrator.py | 66% | 冲突解决成功路径、merge/rebase 策略 |
| **P2** | cli/base.py | 65% | `_auth_preflight_check`、`_terminate_pid` |
| **P2** | compat.py | 68% | Unix 分支 `select`/`read_key` |

---

## 修复优先级排序

### 立即修复（P0）

| 序号 | 编号 | 说明 | 预估工作量 |
|------|------|------|-----------|
| 1 | S1 | `_validate_cmd_str` 换行符/tab/CR 绕过 | 0.5h |
| 2 | OPT-1 | cli/run.py 测试覆盖 47% → 65% (Phase 71) | 4h |

### 短期修复（P1）

| 序号 | 编号 | 说明 | 预估工作量 |
|------|------|------|-----------|
| 3 | S2 | WebSocket 连接数上限 | 0.5h |
| 4 | B1 | `args.resume` 路径遍历防护 | 0.5h |
| 5 | P1 | `_broadcast` 并行发送 | 1h |
| 6 | OPT-2 | integrator.py 测试覆盖 66% → 80% (Phase 72) | 3h |
| 7 | OPT-3 | server.py 测试覆盖 63% → 75% (Phase 73) | 3h |

### 中期优化（P2）

| 序号 | 编号 | 说明 | 预估工作量 |
|------|------|------|-----------|
| 8 | S4 | DENY_PATTERNS 补充 ruby/perl | 0.5h |
| 9 | S5 | ref_content 截断提示 | 0.5h |
| 10 | B3/B4 | Dashboard 加载验证 + summary 异常保护 | 0.5h |
| 11 | P2 | `_read_frame` 提取为方法 | 0.5h |
| 12 | A1 | integrator 策略代码去重 | 2h |
| 13 | B2 | `_rebase_strategy` run_id 参数化 (Phase 72.7) | 0.5h |

---

## Phase 规划

### Phase 77: 安全修复 (P0) 🔴

| # | 任务 | 预估 |
|---|------|------|
| 77.1 | `_validate_cmd_str` 拒绝 `\n`/`\r`/`\t` + 改用 `re.fullmatch` | 修复+测试 |
| 77.2 | 测试：多行命令字符串被拒绝 | 3 tests |

### Phase 78: 安全+健壮性 (P1) 🟡

| # | 任务 | 预估 |
|---|------|------|
| 78.1 | WebSocket `_MAX_CONNECTIONS` 上限 | 修复+测试 |
| 78.2 | `args.resume` 路径验证 | 修复+测试 |
| 78.3 | `_broadcast` 改为 `asyncio.gather()` 并行 | 修复 |
| 78.4 | DENY_PATTERNS 添加 `ruby -e`/`perl -e` | 修复+测试 |

### Phase 79: 代码质量 (P2) 🟢

| # | 任务 | 预估 |
|---|------|------|
| 79.1 | `ref_content` 截断时打印 warning | 修复 |
| 79.2 | `_summary_phase` memory_dir 异常保护 | 修复 |
| 79.3 | Dashboard 加载 kind 值验证 | 修复 |
| 79.4 | `_read_frame` 提取为 `WebSocketConnection` 方法 | 重构 |
| 79.5 | integrator 策略公共逻辑提取 | 重构 |

---

## 并行代码审查结果 (2026-05-23)

> cagent 并行审查：6 个任务，4 个成功，2 个因 stream 解析限制失败。
> 新发现 42 个 OPEN 问题（4 HIGH, 16 MEDIUM, 20 LOW, 2 跨模块）。

### Phase 75: 代码审查修复 — HIGH (P0) 🔴

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 75.1 | git_utils 异常类型统一 | 高 | `run_git` 抛 `RuntimeError`，`run_git_async` 抛 `GitTimeoutError`，统一为 `GitTimeoutError` |
| 75.2 | `_HOOK_SCRIPT` 模板安全 | 高 | `.replace()` 二次替换可能互相干扰，改用 `string.Template` |
| 75.3 | `_is_localhost_origin` scheme 校验 | 高 | 未校验 scheme，`file://localhost` 可绕过，添加 `http/https` 白名单 |
| 75.4 | `_run_lock` 过期锁检测 | 高 | 进程 kill -9 后锁文件残留，获取锁前检查 PID 活跃性 |

### Phase 76: 代码审查修复 — MEDIUM (P1) 🟡

| # | 任务 | 风险 | 说明 |
|---|------|------|------|
| 76.1 | `enable_ansi()` 返回值 | 中 | ctypes 调用未检查返回值，添加 `try/except OSError` |
| 76.2 | `run_git_async` Windows 子进程清理 | 中 | `proc.kill()` 不杀子进程，改用 `taskkill /T` |
| 76.3 | `_validate_agent_id` null byte | 中 | 缺少 `\x00` 检查 |
| 76.4 | `memory.py` OSError 处理 | 中 | `write()`/`append()`/`read()` 未处理磁盘错误 |
| 76.5 | `DENY_PATTERNS` 绝对路径 | 中 | `/usr/bin/git push` 可绕过 |
| 76.6 | close 帧状态码 | 中 | RFC 6455 不合规，解析并回送状态码 |
| 76.7 | `ensure_future` → `create_task` | 中 | Python 3.10+ 废弃 API |
| 76.8 | API key 诊断泄露 | 中 | `_print_auth_diagnostics` 暴露前 8 + 后 4 字符 |
| 76.9 | `auth_ok` 并发写入 | 中 | 使用 `atomic_write` 替代 `write_text` |
| 76.10 | `_is_pid_active` PID 复用 | 中 | Windows 上 PID 复用可能误判 |
| 76.11 | `_run_lock` force 模式 | 中 | `--force` 完全跳过锁，应警告而非跳过 |
| 76.12 | CLI git 操作统一 | 中 | 11 处直接调用 `subprocess.run`，迁移到 `git_utils` |
| 76.13 | symlink 循环保护 | 中 | `_scan_dir_tree` 无限递归风险 |
| 76.14 | `_cleanup_sandbox` 双重执行 | 中 | atexit 被 unregister 后无兜底 |
| 76.15 | `_follow_file` 文件消失检测 | 中 | 删除后无限空读循环 |
| 76.16 | `_cmd_clean --all --force` 确认 | 中 | 误操作永久删除所有记录 |

### 未覆盖的审查范围

| 范围 | 原因 |
|------|------|
| dispatcher.py + integrator.py | cagent task-004 stream 解析失败 |
| test infrastructure | cagent task-006 stream 解析失败 |

> 需手动审查或重跑这两个模块。

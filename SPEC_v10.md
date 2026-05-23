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

# Task Plan

全面代码审查 cagent 项目 — 按关注领域分组，每个任务输出独立的审查报告文件。

## Tasks

### Task 001 — Windows 兼容性审查
- **depends_on**: none
- **files**: `REVIEW_WIN32.md`
- **scope**: `cagent/compat.py`, `cagent/git_utils.py`, `cagent/worktree.py`, `cagent/agent.py`, `cagent/cli/base.py`, `cagent/cli/misc.py`, `bin/cagent`, `bin/cagent.cmd`

检查以下方面：
1. **编码问题**: 文件读写是否指定 `encoding="utf-8"`（中文 Windows 默认 GBK）；subprocess 输出解码是否正确处理非 ASCII；JSON 序列化/反序列化是否处理非 ASCII 路径。
2. **路径问题**: 是否存在硬编码 `/` 路径分隔符；`pathlib.Path` vs 字符串拼接混用；`os.sep` / `os.path.join` 使用是否一致；temp 目录路径是否跨平台安全。
3. **进程管理**: `subprocess` 的 `creationflags` 是否设置 `CREATE_NO_WINDOW`（避免弹出控制台窗口）；信号处理（SIGTERM vs CTRL_C_EVENT）；进程树清理是否覆盖 Windows。
4. **文件锁/原子写入**: `compat.py` 的原子写入在 Windows 上是否真正原子（rename 语义不同）；并发文件访问是否有竞态。
5. **ANSI/终端**: `compat.py` 的 ANSI 启用逻辑是否覆盖 Windows Terminal / ConEmu / VS Code terminal。

输出格式：按严重程度分级（CRITICAL / HIGH / MEDIUM / LOW）的问题清单，每个问题附文件名、行号、问题描述、修复建议。

### Task 002 — 运行时健壮性审查
- **depends_on**: none
- **files**: `REVIEW_RUNTIME.md`
- **scope**: `cagent/dispatcher.py`, `cagent/integrator.py`, `cagent/progress.py`, `cagent/memory.py`, `cagent/tasks.py`, `cagent/log.py`

检查以下方面：
1. **异常处理**: 是否存在裸 `except:` 或 `except Exception:` 吞掉异常；关键路径是否有适当的异常传播；async 任务的异常是否被正确收集和报告。
2. **资源泄漏**: 文件句柄是否用 `with` 管理；async 连接/流是否正确关闭；`asyncio.Task` 是否在取消时正确清理；临时目录/文件是否保证删除。
3. **竞态条件**: 多个 async 任务写共享状态（dashboard、memory、tasks.json）是否有锁保护；dispatcher 的 wave 调度在边界情况下是否有死锁可能。
4. **状态一致性**: 进程崩溃恢复（resume）后状态是否一致；`tasks.json` 写入是否原子；dashboard 数据在异常退出后是否可恢复。
5. **超时与重试**: 超时逻辑是否覆盖所有阻塞操作；重试是否有指数退避和最大次数限制；重试是否幂等。

输出格式：按严重程度分级的问题清单，每个问题附文件名、行号、问题描述、修复建议。

### Task 003 — 安全漏洞审查
- **depends_on**: none
- **files**: `REVIEW_SECURITY.md`
- **scope**: `cagent/safety.py`, `cagent/server.py`, `cagent/config.py`, `cagent/agent.py`, `cagent/integrator.py`, `cagent/cli/base.py`, `cagent/cli/plan.py`

检查以下方面：
1. **命令注入**: `subprocess` 调用是否使用列表参数（非 `shell=True`）；用户输入的任务描述是否可能注入到 git 命令或 shell 命令中；`safety.py` 的命令拦截是否可绕过（编码、别名、管道、子 shell）。
2. **路径遍历**: 任务文件路径、worktree 路径是否经过规范化和边界检查；`integrator.py` 合并时是否可能写入仓库外的路径；`cli/plan.py` 的 `_scan_dir_tree` 是否有路径遍历风险。
3. **XSS/注入**: `server.py` 的 WebSocket/HTTP 响应是否对用户内容做了转义；dashboard JSON 是否可能包含恶意脚本；手工实现的 WebSocket 帧处理是否有协议层漏洞。
4. **配置注入**: `.cagentrc` / `pyproject.toml` 的配置值是否有类型和范围校验；恶意配置是否可能导致任意代码执行。
5. **权限与沙箱**: `safety.py` 的 hook 机制是否足够健壮；`_HOOK_SCRIPT` 本身是否有 shell 注入风险；`prepare_sandbox` 写入的 JSON 是否可被篡改。

输出格式：按 OWASP 风险等级分级的问题清单，每个问题附文件名、行号、问题描述、攻击场景、修复建议。

### Task 004 — 测试覆盖盲区分析
- **depends_on**: none
- **files**: `REVIEW_COVERAGE.md`
- **scope**: `tests/*.py` vs `cagent/*.py`, `cagent/cli/*.py`, `pyproject.toml`

检查以下方面：
1. **模块覆盖**: 对比源码文件和测试文件，找出完全没有测试或测试极其薄弱的模块/函数。
2. **路径覆盖**: 识别源码中的关键分支（错误处理路径、边界条件、平台特定代码）是否被测试覆盖。
3. **Mock 策略**: 检查现有测试的 mock 是否过于宽松（`return_value` 而非验证参数）；是否 mock 了不该 mock 的东西（掩盖了真实 bug）。
4. **集成测试**: e2e 测试是否覆盖了完整的 run → dispatch → integrate 流程；是否有针对崩溃恢复、信号处理的测试。
5. **Edge Cases**: 边界条件测试是否充分（空输入、超大输入、并发、超时、权限错误、非 ASCII 路径等）。

输出格式：按优先级排序的测试缺口清单，每个缺口附当前覆盖情况、建议的测试场景、预估工作量。

### Task 005 — 综合报告汇总
- **depends_on**: 001, 002, 003, 004
- **files**: `REVIEW_REPORT_v10.md`

读取 Task 001-004 的输出文件，汇总为一份结构化的审查报告：
1. **执行摘要**: 总体质量评估、关键指标、与上次审查的对比。
2. **问题汇总表**: 所有问题按严重程度排序的表格（编号、模块、严重程度、类型、一句话描述）。
3. **Top 10 必须修复项**: 影响最大的 10 个问题，附详细分析和修复方案。
4. **修复路线图**: 按优先级和依赖关系排列的修复顺序建议。
5. **测试改进计划**: 基于覆盖盲区的测试补充建议。

## Execution Order

```
Round 1 (并行):  Task 001, Task 002, Task 003, Task 004
                  (四个审查任务互相独立，无文件冲突，完全并行)

Round 2 (串行):  Task 005
                  (依赖 Task 001-004 的输出，汇总生成最终报告)
```

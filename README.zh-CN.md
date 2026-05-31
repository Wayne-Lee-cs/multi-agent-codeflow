<div align="center">

# 🚀 cagent

**并行启动 N 个 Claude Code worker —— 每个独占一个隔离的 git worktree ——<br>再把它们的提交自动合并回同一个分支。**

[![CI](https://github.com/Wayne-Lee-cs/multi-agent-codeflow/actions/workflows/ci.yml/badge.svg)](https://github.com/Wayne-Lee-cs/multi-agent-codeflow/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-809%20passing-success)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-~87%25-brightgreen)](#开发)

[English](README.md) · **简体中文**

</div>

---

手头有一批互相独立的编码任务？把它们写进一个文本或 Markdown 文件，cagent 就会*并发*执行——每个任务一个 `claude -p` 子进程——然后把每一个成功的提交缝合进同一条集成分支，过程中由 AI agent 自动解决合并冲突。实时进度会流式输出到你的终端或浏览器面板。

```bash
pip install cagent          # 或：git clone … && python -m cagent
cagent run tasks.md -j 4    # 同时跑 4 个任务
```

> **v22.0.0** —— 809 个测试，约 87% 覆盖率，零第三方依赖。

**快速跳转：** [特性](#-特性) · [安装](#安装) · [快速上手](#快速上手) · [命令](#命令) · [任务文件格式](#任务文件格式) · [配置](#配置文件) · [安全](#-安全) · [架构](#架构) · [已知限制](#已知限制)

## ✨ 特性

- **并行执行** —— 基于有界并发的异步分发器，并发数通过 `-j` 配置
- **Git worktree 隔离** —— 每个任务在自己的 worktree 中运行，互不干扰
- **依赖图** —— 任务之间可声明 `depends_on`；用 Kahn 算法做拓扑调度与环检测
- **三种集成策略** —— cherry-pick（默认）、merge、rebase，并由 integrator agent 自动解决冲突
- **安全沙箱** —— PreToolUse 钩子拦截 `git push`、`rm -rf`、`bash -c` 等 20+ 种危险模式；token 拆分检测防止绕过参数过滤
- **Token 预算** —— `--max-tokens` 在累计用量超出预算时停止分发新任务
- **退避重试** —— 对瞬时故障（超时、限流、网络错误）采用指数退避重试
- **断点续跑** —— `--resume` 从被中断的运行处继续，跳过已完成的任务
- **运行内记忆** —— 同一次运行中 agent 通过 `RunMemory` 共享上下文
- **实时面板** —— ANSI 终端（`cagent watch`）或 WebSocket 浏览器界面（`cagent watch --web`）
- **跨平台** —— 同时支持 Windows（msvcrt、CREATE_NEW_PROCESS_GROUP）与 Unix（fcntl、SIGTERM）
- **零依赖** —— 纯 Python 3.11+ 标准库，运行时不需要任何第三方包

## 环境要求

- Python >= 3.11
- Git
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)（`claude` 在 PATH 中）
- `claude -p` 必须能完成鉴权 —— 运行 `claude -p "hello"` 验证一下

## 安装

```bash
# 方式 A：在隔离环境中安装 CLI（推荐）
pipx install cagent          # 待发布到 PyPI 后可用
cagent run tasks.txt

# 方式 B：直接从仓库安装（当前即可用，无需 PyPI）
pipx install "git+https://github.com/Wayne-Lee-cs/multi-agent-codeflow.git"

# 方式 C：克隆即运行（零安装）
git clone https://github.com/Wayne-Lee-cs/multi-agent-codeflow.git
cd multi-agent-codeflow
python -m cagent run tasks.txt
```

直接 `pip install cagent`（或在克隆目录里 `pip install .`）同样可行 ——
之所以推荐 `pipx`，只是因为它能让 CLI 与全局 site-packages 隔离开。

开发用（额外装上 mypy、pytest、pytest-asyncio、pytest-cov、build）：

```bash
pip install -e ".[dev]"
python -m pytest          # 运行测试套件
python -m build           # 把 wheel + sdist 构建到 dist/
```

## 快速上手

```bash
# 1. 规划（可选）—— 把一个目标拆解成多个任务
cagent plan "构建一个带鉴权和计费的 REST API"

# 2. 运行 —— 并发执行任务
cagent run tasks.md -j 4

# 3. 监控
cagent watch           # 实时面板
cagent status          # 一次性快照

# 4. 集成
git merge cagent/<run-id>/integration
cagent push cagent/<run-id>/integration
```

## 命令

| 命令 | 说明 |
|------|------|
| `plan <goal>` | 由 architect agent 把目标拆解成互不冲突的任务 |
| `run <tasks-file>` | 并发运行任务（.txt 或 .md） |
| `run --resume <run-id>` | 恢复之前的运行，跳过已完成的任务 |
| `status [run-id]` | 一次性面板快照 |
| `watch [run-id]` | 实时 ANSI 面板（按 `q` 退出） |
| `watch --web [port]` | WebSocket 浏览器面板（默认端口：8080） |
| `log <task-id>` | 显示某个任务的事件 |
| `cancel <task-id>` | 取消正在运行的任务 |
| `clean [run-id]` | 清理 worktree 和分支 |
| `push <branch>` | 推送到 origin（需要 y/N 确认） |
| `branches` | 列出 cagent 分支 |

## 运行选项

```
-j, --jobs N              并发数（默认：4）
--base <branch>           基准分支/SHA（默认：HEAD）
--strategy STR            集成策略：cherry-pick|merge|rebase
--squash                  把集成压缩成单个提交
--timeout <sec>           单个 agent 超时（默认：1800）
--retries N               瞬时错误自动重试（超时/限流/网络）
--max-turns N             单任务轮次上限（透传给 claude -p）
--max-tokens N            单次运行 token 预算（任务之间检查）
--worker-model <id>       worker 使用的模型覆盖
--integrator-model <id>   integrator 使用的模型覆盖
--post-integrate-cmd CMD  集成后的校验命令（例如 "pytest"）
--quiet                   只打印 START/DONE/FAIL 事件
--api-key KEY             显式 API key（更推荐用 ANTHROPIC_API_KEY 环境变量；
                          --api-key 的值会暴露在进程列表中）
--keep-worktrees          运行后保留 worktree
--force                   跳过运行锁检查
--dry-run                 只展示计划好的执行，不实际运行
--fail-on-partial         只要有任意任务失败就以非零退出（默认仅在完全失败
                          或集成失败时非零）
```

**退出码** —— `cagent run` 成功返回 `0`；当无任何任务成功、集成失败、或
`--post-integrate-cmd` 校验失败时返回 `1`。默认情况下*部分*成功（部分任务失败、
部分已集成）仍返回 `0`；加 `--fail-on-partial` 则任意任务失败都使退出码非零。
这样 `cagent run … && deploy` 在 CI 中才安全可靠。

## 任务文件格式

### 纯文本（`.txt`）

每行一个任务。空行和 `#` 注释会被忽略：

```
# 鉴权模块
给设置页面加上登录表单
创建 JWT token 校验中间件

# 计费功能
实现 Stripe 结账流程
```

### Markdown（`.md` —— 由 `cagent plan` 生成）

带依赖关系和文件边界的结构化任务：

```markdown
### Task 001
- **depends_on**: none
- **files**: src/types.py

创建 src/types.py，放置共享数据模型。

### Task 002
- **depends_on**: 001
- **files**: src/users.py

创建 src/users.py，实现用户 CRUD。
```

约定（conventions）从同目录下的 `conventions.md` 加载，或从内联的 `## Conventions` 小节加载。

## 配置文件

cagent 会从配置文件读取默认值，这样你就不必反复敲常用参数。

**查找顺序**（找到第一个即生效）：

1. git 仓库根目录下的 `.cagentrc`（TOML 格式）
2. `pyproject.toml` 里的 `[tool.cagent]` 小节

命令行参数始终覆盖配置文件中的值。

**`.cagentrc` 示例：**

```toml
jobs = 8
timeout = 3600
strategy = "merge"
retries = 2
quiet = true
worker_model = "claude-sonnet-4-6"
integrator_model = "claude-sonnet-4-6"
max_turns = 20
```

**`pyproject.toml` 示例：**

```toml
[tool.cagent]
jobs = 8
timeout = 3600
strategy = "merge"
retries = 2
```

支持的键：`jobs`、`timeout`、`strategy`、`squash`、`quiet`、`retries`、`worker_model`、`integrator_model`、`max_turns`、`max_tokens`、`keep_worktrees`。

## 🛡️ 安全

- cagent **永不自动推送** —— 只有 `cagent push` 并经过显式 y/N 确认才会推
- worker 无法运行 `git push`、`rm -rf`、`node -e`、`python -c`、`powershell -Command` 等破坏性命令（正则 + 基于 token 的检测，包括 `rm -r -f` 这类拆分参数的写法）
- Write、Edit 和 MultiEdit 工具写入的内容会被扫描危险模式（纵深防御）
- 所有工作都发生在隔离的 git worktree 中 —— 你的工作区不会被动到
- 失败的任务会保留其 worktree 以便调试
- Token 预算约束可防止 API 成本失控

## 可观测性

1. **实时输出行** —— `cagent run` 的 stdout 会显示 `[HH:MM:SS] task-NNN <活动>`
2. **实时表格** —— `cagent watch` 提供 ANSI 面板（或 `cagent status` 看快照）
3. **详细回放** —— `cagent log <task-id> -f` 查看完整事件流
4. **Token 跟踪** —— 面板和总结会显示输入/输出 token 数及预算用量

## 架构

```
CLI（argparse + 配置文件）
 │
 ├── Dispatcher（asyncio + 有界信号量）
 │    ├── Task₁ → Agent → git worktree₁
 │    ├── Task₂ → Agent → git worktree₂
 │    └── Task₃ → Agent → git worktree₃
 │
 ├── Integrator（cherry-pick / merge / rebase）
 │    └── 冲突 → integrator agent → 解决
 │
 ├── Dashboard（异步 I/O worker）
 │    ├── ANSI 终端（LinePrinter）
 │    └── WebSocket 服务（DashboardServer）
 │
 └── 安全沙箱（PreToolUse 钩子）
      └── 20+ 拦截模式 + token 拆分检测
```

### 模块地图

| 模块 | 用途 |
|------|------|
| `cagent/cli/` | CLI 入口、子命令、配置加载 |
| `cagent/config.py` | 配置文件加载（.cagentrc、pyproject.toml） |
| `cagent/dispatcher.py` | 异步任务调度、依赖图、重试、预算 |
| `cagent/agent.py` | 子进程管理、流解析、提交 |
| `cagent/integrator/` | 分支集成包 —— `base.py`（共享 git/冲突辅助）+ 每种策略一个文件：`cherry_pick.py`、`merge.py`、`rebase.py` |
| `cagent/safety.py` | 沙箱钩子生成、拦截模式 |
| `cagent/tasks.py` | 任务数据模型、纯文本 / Markdown 解析 |
| `cagent/worktree.py` | Git worktree 增删改查 |
| `cagent/memory.py` | agent 之间的运行内共享记忆 |
| `cagent/progress.py` | 事件解析、面板状态、异步 I/O |
| `cagent/server.py` | HTTP + WebSocket 面板服务 |
| `cagent/log.py` | 基于异步队列的控制台输出 |
| `cagent/git_utils.py` | 带超时的统一 git 命令封装 |
| `cagent/compat.py` | 跨平台兼容层 |

## 已知限制

- **预算超支**：`--max-tokens` 在任务之间检查，并发任务可能超支 `(并发数 - 1)` 个任务的 token 量。
- **Write/Edit 内容误报**：安全钩子会对文件内容应用拦截模式（纵深防御），可能误拦合法写入——比如注释、测试或文档里包含命令字符串的情况。
- **间接执行绕过**：编译后的二进制或非 Bash 解释器（例如一个 Go 程序调用 `exec("git push")`）可以绕过正则/token 安全检查。完全隔离需要 Docker（尚未实现）。
- **rebase 策略基于 cherry-pick**：`--strategy rebase` 内部通过 `git cherry-pick` 回放提交，而非 `git rebase --onto`。对单提交分支二者等价；对多提交分支，每个提交会被独立回放。

## 开发

### 运行测试

```bash
python -m pytest tests/ -v
```

### 带覆盖率运行测试

```bash
python -m pytest tests/ --cov=cagent --cov-report=term-missing
```

### 类型检查

```bash
python -m mypy cagent/
```

## 许可证

MIT

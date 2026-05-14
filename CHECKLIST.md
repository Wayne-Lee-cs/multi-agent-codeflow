# cagent v1 — Implementation Checklist

基于 PLAN.md 的逐步实现清单。每步独立可测，按顺序执行。

---

## Phase 1: 项目骨架

- [x] **1.1** 创建 `.gitignore`，排除 `.cagent/`、`__pycache__/`、`*.pyc`
- [x] **1.2** 创建 `cagent/__init__.py`（空文件）
- [x] **1.3** 创建 `cagent/__main__.py`，含 Python >= 3.11 版本检查，调用 `cli.main()`
- [x] **1.4** 创建 `bin/cagent`（Unix shebang shim），`sys.path` 调整 + `from cagent.cli import main; main()`
- [x] **1.5** 创建 `bin/cagent.cmd`（Windows 批处理入口）：`@echo off` + `python -m cagent %*`
- [x] **1.6** 验证：`python -m cagent --help` 能正常输出（cli.py 还没写，先 stub 一个打 help 的）

## Phase 2: 任务解析 — `cagent/tasks.py`

- [x] **2.1** 定义 `@dataclass Task`：`id, prompt, branch, status, commit_sha, log_path, depends_on`
- [x] **2.2** 实现 `parse_tasks_file(path, run_id) -> list[Task]`：非空非 `#` 行 = 一个任务；id = `f"{idx:03d}"`；branch = `cagent/{run_id}/task-{id}`
- [x] **2.3** 实现 `dump_state(run_dir, tasks)` / `load_state(run_dir) -> list[Task]`：JSON 序列化
- [x] **2.4** 验证：写一个 `tasks/example.txt`，手动调用 `parse_tasks_file` 确认解析正确

## Phase 3: Git Worktree 管理 — `cagent/worktree.py`

- [x] **3.1** 实现 `current_head(repo_root) -> str`：`git rev-parse HEAD`
- [x] **3.2** 实现 `create_worktree(repo_root, worktree_path, branch, base_sha)`：`git worktree add -b <branch> <path> <base_sha>`
- [x] **3.3** 实现 `remove_worktree(repo_root, worktree_path)`：`git worktree remove --force`
- [x] **3.4** 所有 git 调用走 `subprocess.run(check=True, capture_output=True)`，错误时透传 stderr
- [x] **3.5** 验证：创建 worktree → 确认目录存在 → 删除 → 确认清理干净

## Phase 4a: 安全沙箱 — `cagent/safety.py`

- [x] **4a.1** 实现 `prepare_sandbox(worktree_path)`：在 worktree 内创建 `.claude/settings.local.json`
- [x] **4a.2** 注册 `PreToolUse` Bash deny hook，Unix 危险命令正则：`git push` / `git reset --hard` / `git clean -f` / `rm -rf` / `git update-ref` / `git remote (set-url|add)`
- [x] **4a.3** 注册 Windows 危险命令正则：`Remove-Item.*-Recurse.*-Force` / `del /s` / `rd /s`
- [x] **4a.4** 验证：在 worktree 内检查 `.claude/settings.local.json` 内容格式正确

## Phase 4b: 跨平台兼容层 — `cagent/compat.py`

- [x] **4b.1** 实现 `stdin_has_key() -> bool`：Unix `select.select` / Windows `msvcrt.kbhit()`
- [x] **4b.2** 实现 `read_key() -> str`：Unix `sys.stdin.read(1)` / Windows `msvcrt.getwch()`
- [x] **4b.3** 实现 `is_tty() -> bool`：`sys.stdin.isatty()`
- [x] **4b.4** 实现 `enable_ansi()`：Windows `os.system("")` 启用 VT100；Unix no-op
- [x] **4b.5** 实现 `atomic_write(path, content)`：写 tmp + `os.replace()`（跨平台安全）
- [x] **4b.6** 验证：在 Windows 上运行 `enable_ansi()` 后打印 ANSI 彩色文本确认生效

## Phase 4c: Agent 子进程 — `cagent/agent.py`

- [x] **4c.1** 定义 `@dataclass AgentResult`：`task_id, status, commit_sha, fail_reason`
- [x] **4c.2** 实现 `async run_agent(task, worktree_path, run_dir, timeout, model_override)`
- [x] **4c.3** 命令组装：`claude -p <prompt> --permission-mode acceptEdits --output-format stream-json --verbose`；有 `model_override` 时追加 `--model`
- [x] **4c.4** Prompt 传递策略：长度 > 8000 或含 `"` / `\` / 换行时走 stdin pipe，否则 `-p` 参数
- [x] **4c.5** env 直接 `os.environ.copy()`，不裁剪不注入
- [x] **4c.6** stdout 逐行读取：追加到 log 文件 + 喂给 EventParser + 推事件到 dashboard queue
- [x] **4c.7** 超时处理：`proc.kill()` → 状态置 failed
- [x] **4c.8** 进程退出后统一提交：`git status --porcelain` 有变更 → `git add -A && git commit`；无变更 → 标 `noop`
- [x] **4c.9** 验证：手动跑一个简单 task，确认 log 文件生成、commit 产生

## Phase 5: 事件解析与观测 — `cagent/progress.py`

- [x] **5.1** 定义 `@dataclass Event`：`ts, kind, summary, raw`
- [x] **5.2** 定义 `@dataclass TaskProgress`：`task_id, status, started_at, ended_at, last_event, last_activity, tool_count, bytes_seen, commit_sha, fail_reason`
- [x] **5.3** 实现 `EventParser.feed(line) -> Event | None`：解析 stream-json 各事件类型
  - [x] `system.init` → `kind="start"`
  - [x] `assistant.content_block_start.tool_use` → `kind="tool_use"`，提炼工具名 + 关键参数
  - [x] `user.tool_result` → `kind="tool_result"`；deny 类标 `kind="denied"`
  - [x] `assistant.content_block_start.text` → `kind="text"`
  - [x] `assistant.content_block_start.thinking` → `kind="thinking"`
  - [x] `result` → `kind="done"`
  - [x] 解析失败 fallback：`Event(kind="text", summary=line[:80])`
- [x] **5.4** 实现 `Dashboard`：维护 `dict[task_id, TaskProgress]`，每次事件更新后原子写 `dashboard.json`
- [x] **5.5** 验证：用预录的 stream-json 样本喂 EventParser，确认解析正确

## Phase 6: 并发调度 — `cagent/dispatcher.py`

- [x] **6.1** 实现 `async run(tasks, concurrency, run_dir, base_sha, repo_root, worker_model_override, timeout)`
- [x] **6.2** `asyncio.Semaphore(concurrency)` 控制并发
- [x] **6.3** 每任务协程：拿信号量 → 建 worktree → `agent.run_agent` → 释放
- [x] **6.4** `asyncio.TaskGroup` 等待全部完成，单个失败不阻断其余
- [x] **6.5** 每次状态变化调 `tasks.dump_state` 更新 `tasks.json`
- [x] **6.6** 持有 Dashboard 实例，聚合各 worker 事件
- [x] **6.7** 验证：用 2 个简单任务 + `-j 2` 确认并发执行 + worktree 隔离

## Phase 7: 集成器 — `cagent/integrator.py`

- [x] **7.1** 实现 `integrate(tasks, run_dir, base_sha, repo_root, squash, integrator_model_override, timeout) -> str`
- [x] **7.2** 创建 integration worktree + 分支 `cagent/{run_id}/integration`
- [x] **7.3** 按 tasks 顺序 cherry-pick 状态为 `done` 的任务 commit
- [x] **7.4** 冲突检测：`git status --porcelain` 看 `UU` 标记
- [x] **7.5** 冲突解决：组装 integrator prompt → `claude -p` → 检查结果 → `git add -A && GIT_EDITOR=true git cherry-pick --continue`
- [x] **7.6** 解决失败处理：整体失败，保留现场
- [x] **7.7** `--squash` 模式：`git reset --soft <base_sha> && git commit -m "<summary>"`
- [x] **7.8** 返回 integration 分支末端 SHA
- [x] **7.9** 验证：用冲突任务测试 integrator agent 解冲突流程

## Phase 8: CLI 入口 — `cagent/cli.py`

- [x] **8.1** argparse 主解析器 + 子命令结构
- [x] **8.2** `run` 子命令：`tasks-file` 位置参数 + `-j` / `--base` / `--squash` / `--keep-worktrees` / `--worker-model` / `--integrator-model` / `--timeout` / `--quiet`
- [x] **8.3** `status` 子命令：读 `dashboard.json` 渲染表格
- [x] **8.4** `watch` 子命令：轮询 `dashboard.json` + ANSI 重绘；非 TTY 退化为单次 status
- [x] **8.5** `log` 子命令：tail `events.jsonl`，`-f` follow
- [x] **8.6** `clean` 子命令：清理 worktree + 分支 + 日志
- [x] **8.7** `push` 子命令：显示 commit 列表 → y/N 确认 → `git push -u origin <branch>`；不提供 `--force`
- [x] **8.8** `plan` 子命令：v1 留 stub（打印 "coming in v2"）
- [x] **8.9** `main()` 编排：args → run_dir → parse tasks → base_sha → dispatcher.run → integrator.integrate → summary.md → 清理 worktree → 打印结果
- [x] **8.10** Worktree 清理策略实现：全成功删 worktree 保分支；部分失败保留失败的 worktree；`--keep-worktrees` 全保留
- [x] **8.11** 验证：`python -m cagent run tasks/example.txt -j 2` 全流程跑通

## Phase 9: 控制台日志 — `cagent/log.py`

- [x] **9.1** 实现 `LinePrinter`：订阅 dashboard 事件 queue，stdout 打印 `[HH:MM:SS] task-NNN <activity>` 格式
- [x] **9.2** `--quiet` 模式：只打 START / DONE / FAIL / DENIED
- [x] **9.3** integrator 阶段日志：cherry-pick / conflict / resolver 状态
- [x] **9.4** 验证：`cagent run` 输出格式符合 PLAN.md 示例

## Phase 10: Slash Command — `.claude/commands/cagent.md`

- [x] **10.1** 创建 `.claude/commands/cagent.md`，含 frontmatter（description / argument-hint）
- [x] **10.2** 指令内容：调用 `python -m cagent $ARGUMENTS`，注明 run 是长时命令、push 需要交互
- [x] **10.3** 验证：在 Claude Code 会话里 `/cagent --help` 能正常触发

## Phase 11: 示例与文档

- [x] **11.1** 创建 `tasks/example.txt`：两条不重叠任务（造文件 A、造文件 B）
- [x] **11.2** 更新 `README.md`：使用说明（三种入口 + 模型零配置 + 安全承诺）

---

## Phase 12: Round 2 Bug Fixes

### HIGH severity
- [x] **12.1** `integrator.py` — `_has_conflict_markers` now detects `AA` (both-added) in addition to `DD`/`UU`
- [x] **12.2** `integrator.py` — `_resolve_conflicts` conflict file extraction also handles `AA`
- [x] **12.3** `integrator.py` — `git add -A` in `_resolve_conflicts` wrapped in try/except, aborts cherry-pick on failure
- [x] **12.4** `integrator.py` — `_cherry_pick_one` wrapped in try/except in `integrate()` loop

### MEDIUM severity
- [x] **12.5** `progress.py` — `Dashboard` added `_on_event` callback; `set_task_status` creates synthetic events and notifies printer
- [x] **12.6** `progress.py` — `Dashboard.__init__` loads existing `dashboard.json` data (resume support)
- [x] **12.7** `progress.py` — `EventParser._parse_user` guards against empty tool_result content list
- [x] **12.8** `agent.py` — `_commit_result` includes git commit stderr in `AgentResult.fail_reason`
- [x] **12.9** `agent.py` — `git rev-parse HEAD` returncode checked after commit
- [x] **12.10** `cli.py` — Replaced monkey-patching with `dashboard._on_event` callback
- [x] **12.11** `cli.py` — Watch table ANSI alignment fixed (pad before adding ANSI codes)
- [x] **12.12** `cli.py` — `_find_run_dir` accepts dirs with `tasks.json` (not just `dashboard.json`)
- [x] **12.13** `cli.py` — Resume cleans stale worktrees/branches before re-running
- [x] **12.14** `cli.py` — Resume cleans worktrees on Ctrl+C
- [x] **12.15** `cli.py` — `input()` in clean/push handles `EOFError`
- [x] **12.16** `cli.py` — `log -f` shows existing content before following
- [x] **12.17** `cli.py` — `_clean_worktrees` uses `result_map` instead of `zip` (no silent truncation)

### LOW severity
- [x] **12.18** `integrator.py` — Conflict marker count fixed in integrator prompt (7 chars each)
- [x] **12.19** `safety.py` — Windows deny patterns have `^\s*` anchors

### Deferred (acceptable for v1)
- [ ] **12.20** `integrator.py` — Empty prompt when first task conflicts with base (edge case)
- [ ] **12.21** `integrator.py` — `_run_git` has no timeout (acceptable for CLI tool)
- [ ] **12.22** `cli.py` — `lstrip("* ")` fragile branch parsing (works in practice)
- [ ] **12.23** `cli.py` — `run_id` timestamp collision potential (1-second resolution sufficient)

---

## 验收测试

### 冒烟测试
- [ ] `python -m cagent run tasks/example.txt -j 2` → 两个 task done，integration 分支含两个文件

### 冲突测试
- [ ] 两条都改 README.md → integrator 解冲突 → 最终无冲突标记

### 安全测试
- [ ] worker 执行 `git push` → 被 sandbox 拦截（denied），task 不因此 failed
- [ ] worker 执行 `rm -rf` → 被 sandbox 拦截
- [ ] `cagent push` 输入 `n` / 回车 / Ctrl-C → 无 push 发生
- [ ] Windows: `Remove-Item -Recurse -Force` → 被 sandbox 拦截

### Observability 测试
- [ ] `cagent run` stdout 有 START / tool_use / DONE 行
- [ ] `cagent watch` 在 TTY 下 1s 刷新表格，`q` 退出
- [ ] `cagent watch` 在非 TTY 下退化为单次 status
- [ ] `cagent log task-001` 输出事件流

### 模型跟随测试
- [ ] 默认不传 `--model`，worker 继承主会话 env
- [ ] `--worker-model claude-haiku-4-5` 时 worker 命令行多出 `--model`

### 错误路径测试
- [ ] 不可执行任务 → 标 noop，integrator 跳过
- [ ] `--timeout 1` → 标 failed，integrator 合入成功部分

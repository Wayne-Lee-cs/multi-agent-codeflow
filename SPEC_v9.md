# cagent v9.0 — Bug 修复与优化规格说明

> 第三次全面代码审查发现 6 个 Bug + 6 个优化方向。460 tests, 68% coverage 基线。

---

## Bug 清单

### BUG-1: `integrator._run_claude_agent` stdin drain/close 缺少超时保护 (P0)

**位置**: `integrator.py:249-253`
**影响**: integrator agent 的 stdin 管道阻塞时，进程永久挂起，无法自动恢复。
**根因**: `agent.py:158-164` 已有 30s/5s 超时保护，但 `integrator.py` 中同构代码遗漏。

```python
# 当前代码（integrator.py:249-253）— 无超时
proc.stdin.write(prompt.encode("utf-8"))
await proc.stdin.drain()           # 可能永远阻塞
proc.stdin.close()
await proc.stdin.wait_closed()     # 可能永远阻塞

# 修复方案 — 与 agent.py 一致
proc.stdin.write(prompt.encode("utf-8"))
await asyncio.wait_for(proc.stdin.drain(), timeout=30)
proc.stdin.close()
try:
    await asyncio.wait_for(proc.stdin.wait_closed(), timeout=5)
except (TimeoutError, OSError):
    pass
```

**测试**: mock `proc.stdin.drain()` 超时 → 验证不挂起 + 返回 None。

---

### BUG-2: `integrator._run_claude_agent` 缺少 `FileNotFoundError` 处理 (P0)

**位置**: `integrator.py:236`
**影响**: claude CLI 不在 PATH 时，`create_subprocess_exec` 抛出未捕获的 `FileNotFoundError`，traceback 对用户不友好。
**对比**: `agent.py:121-141` 正确捕获 `FileNotFoundError` 和 `OSError`。

```python
# 修复方案
try:
    proc = await asyncio.create_subprocess_exec(*cmd, ...)
except FileNotFoundError:
    return None  # 调用方已处理 None 返回
except OSError:
    return None
```

**测试**: mock `asyncio.create_subprocess_exec` 抛出 `FileNotFoundError` → 返回 None。

---

### BUG-3: `RunMemory` 缺少 `agent_id` 路径遍历验证 (P1)

**位置**: `memory.py:28,33,42`
**影响**: `write/append/read` 方法直接拼接 `agent_id` 到文件路径，无 `..`/`/`/`\` 检查。
**当前调用方**: 传入 `{counter:03d}` 格式 ID 或 `_integrator` 字面量，安全。
**风险**: 未来新增调用方传入用户控制的 ID 时可能导致目录穿越。

```python
# 修复方案 — 复用 progress.py 已有的校验逻辑
def _validate_agent_id(agent_id: str) -> str:
    if not agent_id or ".." in agent_id or "/" in agent_id or "\\" in agent_id:
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    return agent_id
```

**测试**: `write("../../../etc/passwd", "x")` → `ValueError`。

---

### BUG-4: `_extract_prompt` 误过滤 prompt 中的 markdown field 格式行 (P1)

**位置**: `tasks.py:200`
**影响**: 若任务 prompt 包含 `- **endpoint**: /users` 形式的行，会被错误跳过。

```python
# 当前代码
if re.match(r"^\s*-\s*\*\*\w+\*\*\s*:", stripped):
    continue  # 跳过 field 行

# 修复方案 — 只在 heading 之后、空行之前视为 field
# 遇到第一个非 field 非空行后，不再检查 field 模式
past_fields = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("### Task"):
        continue
    if not past_fields and re.match(r"^\s*-\s*\*\*\w+\*\*\s*:", stripped):
        continue
    if stripped:
        past_fields = True
    if past_fields:
        prompt_lines.append(line)
```

**测试**: prompt 含 `- **word**: value` → 该行保留在解析结果中。

---

### BUG-5: CORS preflight 对无 Origin 请求返回 `Access-Control-Allow-Origin: *` (P2)

**位置**: `server.py:401-405 → _send_cors_preflight:579`
**影响**: 无 Origin header 的 OPTIONS 请求返回 `Allow-Origin: *`，同机器恶意网页可跨域访问 `/api/data`。

```python
# 修复方案 — 无 Origin 时不设置 CORS 头
async def _send_cors_preflight(self, writer, origin):
    if not origin:
        await self._send_http_response(writer, 204, b"")
        return
    # ... existing logic for localhost origin
```

**测试**: OPTIONS 请求无 Origin → 响应不含 `Access-Control-Allow-Origin`。

---

### BUG-6: `_validate_cmd_str` 允许反引号 (P2)

**位置**: `integrator.py:168`
**影响**: 反引号 `` ` `` 在 bash 中触发命令替换。虽然 `--post-integrate-cmd` 来自 CLI 用户（可信），但字符验证的目的是拒绝危险字符，应保持一致性。

```python
# 当前正则
pattern = r'^[\w .\-\/\\:=+,@~()\[\]{}|&;!?\*#$%^\'"<>`]+$'

# 修复方案 — 移除反引号
pattern = r'^[\w .\-\/\\:=+,@~()\[\]{}|&;!?\*#$%^\'"<>]+$'
```

**测试**: `` pytest `echo hacked` `` → 返回 False。

---

## 优化清单

### OPT-1: Dashboard 全量快照效率低 (P2)

**位置**: `progress.py:494-515`
**现状**: 每次 `_write_dashboard` 调用 `get_snapshot()` 遍历所有 task（O(N)），再逐一比较。
**优化**: 已有 `_dirty_progress` 集合跟踪变更 task，改为只序列化脏 task。

```python
# 优化方案
def _write_dashboard(self, force=False):
    ...
    # 只序列化变化的 task（O(dirty) 而非 O(all)）
    diff = {}
    for tid in list(self._dirty_progress):  # 已被 _flush_io 清空
        if tid in self.tasks:
            diff[tid] = _task_progress_dict(self.tasks[tid])
    # 保留全量快照用于写入，但只重新序列化脏 task
    self._last_dashboard_snapshot.update(diff)
    ...
```

**收益**: 100 task 场景下，每次事件更新的序列化从 O(100) 降至 O(1)。

---

### OPT-2: `_truncate_jsonl_if_large` 全量读入内存 (P3)

**位置**: `progress.py:195-212`
**现状**: 5MB 文件全量 `read_text().splitlines()` → 内存峰值 ~10MB。
**优化**: 从文件尾部 seek 查找截断点，只读写尾部内容。

---

### OPT-3: `_resolve_claude` lru_cache 负缓存问题 (P3)

**位置**: `agent.py:24`
**现状**: `@lru_cache(maxsize=1)` 缓存首次查找结果。若 claude 不在 PATH（返回 `"claude"` fallback），后续即使 PATH 修复也不重新查找。
**优化**: 只缓存正值（成功找到的路径），失败时不缓存。

---

### OPT-4: rebase 策略命名与实现不一致 (P3)

**位置**: `integrator.py:791-879`
**现状**: `_rebase_strategy` 内部逐个 cherry-pick，非真正 `git rebase`。对多提交分支行为有差异。
**建议**: 文档注明实现等价于 "replay" 策略，或实现真正 `git rebase --onto`。

---

### OPT-5: 测试覆盖率提升 (P2)

**目标**: 68% → 75%

| 模块 | 当前 | 目标 | 重点 |
|------|------|------|------|
| `cli/__init__.py` | 15% | 50%+ | `main()` argparse 路由 |
| `cli/logcmd.py` | 47% | 70%+ | follow 模式 + kind 过滤 |
| `cli/run.py` | 49% | 65%+ | `_cmd_run_inner` 核心路径 |
| `cli/watch.py` | 49% | 65%+ | ANSI 渲染 + 非 TTY 退化 |
| `server.py` | 53% | 70%+ | WebSocket 心跳 + 帧处理 |

---

### OPT-6: EventParser 非 JSON 行优化 (P3)

**位置**: `progress.py:56-63`
**现状**: 每个非 JSON 行都创建 `Event` 对象。高频输出时产生大量短期对象。
**优化**: 合并连续非 JSON 行，或增加速率限制（如每秒最多创建 10 个 text event）。

---

## Phase 70 — 代码审查修复 (2026-05-23 第四轮审查)

> 576 tests, 76% coverage 基线。代码审查发现 4 P2 + 6 P3 = 10 个问题。

### BUG-7: Dashboard commit SHA 冲突解决后不同步 (P2)

**位置**: `integrator.py:621-622`
**影响**: `_resolve_conflicts` 更新 `task.commit_sha` 和 `task.status`，但未调用 `dashboard.set_task_status`。Dashboard 保留冲突解决前的旧 SHA。
**修复**: 添加 `dashboard.set_task_status(task.id, "done", commit_sha=task.commit_sha)`。

### BUG-8: squash 路径无回滚保护 (P2)

**位置**: `integrator.py:144-149`
**影响**: `git reset --soft` + `git commit` 无 try/except。commit 失败时 worktree 残留 staged changes + detached HEAD，无恢复路径。
**修复**: commit 使用 `check=False`，失败时 `git reset --hard base_sha` 回滚。

### BUG-9: `_validate_task_id` 允许 Windows 非法字符 (P2)

**位置**: `progress.py:230`
**影响**: 拒绝 `..`、`/`、`\` 但允许 `:`、`*`、`?`、`"`、`<`、`>`、`|`（Windows 文件名非法字符）。当前 task ID 均为数字字符串，不可利用，但防御层应严密。
**修复**: 改为 `re.match(r'^[a-zA-Z0-9_-]+$', task_id)`。

### BUG-10: branch name 硬编码假设 (P2)

**位置**: `integrator.py:739`
**影响**: `integration_branch.split('/')[1]` 假设 `cagent/{run_id}/integration` 格式。格式变更时静默产生错误分支名。
**修复**: `_merge_strategy` 新增 `run_id` 参数，显式传入。

### BUG-11: whitespace-only prompt 空 commit message (P3)

**位置**: `agent.py:341`
**影响**: `task.prompt` 为空白时 `first_line` 为空，commit message 变为 `"task 001: "`。
**修复**: `first_line = ... or "(no description)"`。

### BUG-12: conflict prompt 无上限增长 (P3)

**位置**: `integrator.py:489-496`
**影响**: `merged_summaries` 随 integrated task 数量线性增长，50 个 task 约 2KB context。无整体上限。
**修复**: 添加 `_MAX_SUMMARIES_CHARS = 2000` 截断。

### BUG-13: sandbox 清理硬编码路径 (P3)

**位置**: `integrator.py:573-580`
**影响**: 清理 `.claude/settings.local.json` 和 `.claude/hooks/cagent-guard.py` 两个固定路径。扩展 `prepare_sandbox` 写入新文件时会被遗漏。
**修复**: `shutil.rmtree(claude_dir)` 清理整个 `.claude/` 目录。

### 已知可接受问题 (P3, 不修复)

| # | 问题 | 原因 |
|---|------|------|
| 5 | `server.py:622` CORS 空 origin 返回 `*` | 本地开发工具，非浏览器客户端不发 Origin，风险可接受 |
| 7 | `dispatcher.py:192` O(n) token 预算检查 | 典型 task 数 <100，无实际性能影响 |
| 9 | `compat.py:69` mkstemp 孤立临时文件 | 进程崩溃极端场景，run 结束时目录被清理 |

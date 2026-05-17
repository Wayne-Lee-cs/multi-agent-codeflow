# Benchmark Report: cagent vs Single Agent

**日期**: 2026-05-17  
**任务**: 创建 3 个 Python 工具模块 + 1 个 README 文档 (tasks/e2e_project.txt)  
**模型**: mimo-v2.5-pro  
**环境**: Windows 11, Python 3.12.7

---

## 时间对比

| 指标 | cagent (-j 4) | 单 agent | 差异 |
|------|--------------|----------|------|
| **总耗时** | 3m11s | 1m23s | cagent 慢 2.3x |
| Agent 数量 | 4 并行 + 1 integrator | 1 | — |
| 总 agent 计算时间 | 247s (4 worker) + 62s (integrator) | 81s | cagent 4x 更多计算 |
| 冲突解决 | 有 (task 4 vs tasks 1-3) | 无 | cagent 额外开销 |

### 各任务耗时 (cagent)

| 任务 | 耗时 | tools | 说明 |
|------|------|-------|------|
| task-001 (string_utils) | 37s | 8 | 有权限确认开销 |
| task-002 (time_utils) | 17s | 1 | 最快 |
| task-003 (file_utils) | 75s | 4 | PowerShell 兼容问题 |
| task-004 (README) | 1m58s | 21 | 需读取其他 3 个文件 + 解冲突 |
| integrator | 1m2s | 11 | cherry-pick 冲突解决 |

---

## 产出物对比

| 指标 | cagent | 单 agent |
|------|--------|----------|
| 文件数 | 4 | 4 |
| 总行数 | 369 | 203 |
| string_utils.py 函数数 | 8 | 5 |
| time_utils.py 函数数 | 13 | 5 |
| file_utils.py 函数数 | 10 | 5 |
| README.md 行数 | 109 | 75 |
| Python 语法检查 | 全部通过 | 全部通过 |

### 功能覆盖度

| 需求函数 | cagent | 单 agent |
|----------|--------|----------|
| camel_to_snake | ✅ | ✅ |
| snake_to_camel | ✅ | ✅ |
| truncate | ✅ | ✅ |
| slugify | ✅ | ✅ |
| is_palindrome | ✅ | ✅ |
| now_iso | ✅ | ✅ |
| parse_iso | ✅ | ✅ |
| humanize_duration | ✅ | ✅ |
| is_business_hours | ✅ | ✅ |
| days_ago | ✅ | ✅ |
| ensure_dir | ✅ | ✅ |
| read_json | ✅ | ✅ |
| write_json | ✅ | ✅ |
| file_hash | ✅ | ✅ |
| find_files | ✅ | ✅ |

**需求覆盖率**: 两者均为 15/15 (100%)

---

## 质量对比

### cagent 优势
- **更丰富的实现**: 多出了 `reverse_string`、`count_words`、`extract_emails`、`now_utc`、`add_business_days` 等额外函数
- **更详细的文档**: README 109 行 vs 75 行，函数表格更完整
- **隔离性**: 每个 agent 在独立 worktree 中工作，互不干扰
- **可观测性**: 完整的事件流、dashboard、日志

### 单 agent 优势
- **更快**: 1m23s vs 3m11s (快 2.3x)
- **无冲突**: 不需要 integrator，产出物直接可用
- **更精确**: 严格按需求创建 5+5+5 个函数，无多余代码
- **更简单**: 无 worktree 管理、无 cherry-pick、无冲突解决开销

### cagent 的额外函数（超出需求）

| 模块 | 额外函数 |
|------|----------|
| string_utils | reverse_string, count_words, extract_emails |
| time_utils | now_utc, format_timestamp, parse_timestamp, time_ago, add_business_days, get_week_range, duration_string, is_between |
| file_utils | read_text, write_text, list_files, file_size_human, safe_filename |

---

## 分析

### 为什么 cagent 更慢？

1. **任务依赖**: task-004 (README) 依赖 tasks 1-3 的产出。cagent 并发执行导致 task-004 创建了独立版本的 3 个模块文件，cherry-pick 时产生冲突
2. **冲突解决开销**: integrator agent 需要 1m2s 来读取冲突文件、合并两边实现、验证无冲突标记
3. **权限确认**: worker 001 和 integrator 遇到 PowerShell 命令权限确认，增加了延迟
4. **worktree 管理**: 创建/删除 5 个 git worktree 有固定开销

### 何时 cagent 更有优势？

- **任务完全独立**: 无依赖关系时，4 个任务真正并行，总耗时 ≈ 最慢单任务耗时
- **大规模任务**: 10+ 个独立任务时，并行收益远超管理开销
- **复杂任务**: 每个任务需要 2-5 分钟时，cagent 的并行优势更明显
- **需要隔离**: 不同任务修改同一文件的不同部分时，worktree 隔离避免中间状态冲突

### 何时单 agent 更好？

- **任务有依赖**: 后续任务依赖前序产出时，串行执行更自然
- **小规模任务**: 2-4 个简单任务，单 agent 一次搞定更快
- **精确控制**: 需要严格按需求实现、不想要多余代码时

---

## 结论

| 场景 | 推荐 | 原因 |
|------|------|------|
| 4 个简单任务，有依赖 | **单 agent** | 快 2.3x，无冲突，产出更精确 |
| 10+ 个独立任务 | **cagent** | 并行收益显著 |
| 复杂任务 (每任务 3min+) | **cagent** | 并行节省的时间 > 管理开销 |
| 需要代码隔离 | **cagent** | worktree 天然隔离 |

**本次 benchmark**: 单 agent 更优 (1m23s vs 3m11s，产出更精确)

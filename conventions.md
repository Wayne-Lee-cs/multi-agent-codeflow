# Global Conventions

## Language & Runtime
- Python 3.11+，仅使用标准库（零运行时依赖）
- 所有函数必须有类型注解（包括返回值）
- 使用 `from __future__ import annotations` 延迟注解求值

## Code Style
- 函数/变量：`snake_case`
- 类：`PascalCase`
- 常量：`UPPER_SNAKE_CASE`
- 私有成员：`_leading_underscore`
- 行宽上限：120 字符
- 字符串：优先使用双引号 `"`

## Docstring 规范
- 公共函数/类：单行 docstring，描述用途
- 复杂逻辑：Google-style docstring（Args / Returns / Raises）
- 不为显而易见的代码添加 docstring

## 异步规范
- 异步函数统一使用 `async def`
- 子进程使用 `asyncio.create_subprocess_exec`（非 `subprocess.run`）
- 超时使用 `asyncio.wait_for` 或 `asyncio.timeout`
- 资源清理使用 `try/finally` 或 `async with`

## 安全规范
- Shell 命令：禁止 `shell=True`，使用参数列表
- 文件路径：使用 `pathlib.Path`，禁止字符串拼接
- 用户输入：必须校验后再使用（agent ID、task ID、路径等）
- Git 操作：通过 `git_utils.py` 统一调用，禁止直接 `subprocess`

## 错误处理
- 使用自定义异常类继承 `Exception`
- 异常消息包含足够的上下文信息
- 不吞掉异常（`except: pass`）
- 清理逻辑放在 `finally` 块中

## 测试规范
- 测试文件：`tests/test_{module}.py`
- 测试函数：`test_{功能}_{场景}`
- 使用 `pytest.mark.asyncio` 标记异步测试
- Mock 外部依赖（subprocess、文件系统），不 mock 内部逻辑
- 每个测试独立，不依赖执行顺序

## Review 产出要求
- 每个任务生成独立的审查报告文件（`REVIEW_*.md`）
- 每个发现标注类型标签：
  - **BUG**：确认的缺陷（附重现条件）
  - **SECURITY**：安全漏洞（附攻击场景）
  - **PERFORMANCE**：性能问题（附影响范围）
  - **QUALITY**：代码质量问题（附改进建议）
- 严重程度分级：CRITICAL / HIGH / MEDIUM / LOW
- 每个发现附带具体文件路径和行号

## 文件边界约束
- 每个任务只写入自己负责的 `REVIEW_*.md` 文件
- 不得修改源码文件、测试文件、配置文件
- Task 005 汇总时只读取其他任务的输出，不修改

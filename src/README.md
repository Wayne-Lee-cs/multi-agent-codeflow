# Utility Modules

Python utility modules for common string, time, and file operations.

## string_utils.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `camel_to_snake` | `(name: str) -> str` | Convert camelCase/PascalCase to snake_case |
| `snake_to_camel` | `(name: str) -> str` | Convert snake_case to camelCase |
| `truncate` | `(s: str, max_len: int, suffix: str = "...") -> str` | Truncate string to max_len with suffix |
| `slugify` | `(s: str) -> str` | Convert string to URL-friendly slug |
| `is_palindrome` | `(s: str) -> bool` | Check if string is a palindrome (case-insensitive) |

```python
from src.string_utils import camel_to_snake, slugify, truncate

camel_to_snake("getUserName")  # "get_user_name"
slugify("Hello World! Test")   # "hello-world-test"
truncate("Long text here", 10) # "Long te..."
```

## time_utils.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `now_iso` | `() -> str` | Current UTC time as ISO 8601 string |
| `parse_iso` | `(s: str) -> datetime` | Parse ISO 8601 string to datetime |
| `humanize_duration` | `(seconds: int \| float) -> str` | Duration to human-readable string (e.g., "2h 30m") |
| `is_business_hours` | `(dt: datetime) -> bool` | Check if datetime is Mon-Fri 9:00-17:00 |
| `days_ago` | `(n: int) -> datetime` | UTC datetime n days ago |

```python
from src.time_utils import now_iso, humanize_duration, days_ago

now_iso()              # "2026-05-17T12:00:00+00:00"
humanize_duration(3661) # "1h 1m 1s"
days_ago(7)            # datetime 7 days ago
```

## file_utils.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `ensure_dir` | `(path: str \| Path) -> Path` | Create directory and parents if needed |
| `read_json` | `(path: str \| Path) -> Any` | Read and parse a JSON file |
| `write_json` | `(path: str \| Path, data: Any, indent: int = 2) -> None` | Write data as JSON |
| `file_hash` | `(path: str \| Path, algorithm: str = "sha256") -> str` | Compute file hash digest |
| `find_files` | `(directory: str \| Path, pattern: str) -> list[str]` | Find files matching glob pattern |

```python
from src.file_utils import write_json, read_json, file_hash, find_files

write_json("config.json", {"debug": True})
data = read_json("config.json")
file_hash("config.json")          # SHA-256 hex
find_files("src", "**/*.py")      # all Python files
```

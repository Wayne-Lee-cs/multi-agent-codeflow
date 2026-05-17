"""File and directory utility functions."""

import hashlib
import json
from pathlib import Path
from typing import Any


def ensure_dir(path: str | Path) -> Path:
    """Create a directory (and parents) if it doesn't exist.

    Args:
        path: Directory path to create.

    Returns:
        The Path object for the created/existing directory.

    Examples:
        >>> ensure_dir("output/reports")
        PosixPath('output/reports')
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: str | Path) -> Any:
    """Read a JSON file and return the parsed object.

    Args:
        path: Path to the JSON file.

    Returns:
        The parsed JSON data.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file contains invalid JSON.

    Examples:
        >>> data = read_json("config.json")
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    """Write a Python object to a file as formatted JSON.

    Creates parent directories if needed.

    Args:
        path: Output file path.
        data: JSON-serializable Python object.
        indent: Number of spaces for indentation (default 2).

    Examples:
        >>> write_json("output.json", {"key": "value"})
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def file_hash(path: str | Path, algorithm: str = "sha256") -> str:
    """Compute the hex digest of a file using the given hash algorithm.

    Args:
        path: Path to the file.
        algorithm: Hash algorithm name (default 'sha256').

    Returns:
        Hex-encoded hash digest string.

    Raises:
        FileNotFoundError: If the file does not exist.

    Examples:
        >>> file_hash("data.csv")
        'a1b2c3d4...'
    """
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def find_files(directory: str | Path, pattern: str) -> list[str]:
    """Find files matching a glob pattern under a directory.

    Args:
        directory: Root directory to search.
        pattern: Glob pattern (e.g., '*.py', '**/*.txt').

    Returns:
        Sorted list of matching file paths as strings.

    Examples:
        >>> find_files("src", "**/*.py")
        ['src/__init__.py', 'src/main.py']
    """
    p = Path(directory)
    return sorted(str(f) for f in p.rglob(pattern) if f.is_file())

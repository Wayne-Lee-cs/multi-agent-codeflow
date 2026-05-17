"""String utility functions for common text operations."""

import re


def camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case.

    Args:
        name: A camelCase or PascalCase string.

    Returns:
        The snake_case equivalent.

    Examples:
        >>> camel_to_snake("getUserName")
        'get_user_name'
        >>> camel_to_snake("HTTPSResponse")
        'https_response'
    """
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase.

    Args:
        name: A snake_case string.

    Returns:
        The camelCase equivalent.

    Examples:
        >>> snake_to_camel("get_user_name")
        'getUserName'
    """
    parts = name.split("_")
    return parts[0].lower() + "".join(word.capitalize() for word in parts[1:])


def truncate(s: str, max_len: int, suffix: str = "...") -> str:
    """Truncate a string to max_len characters, appending suffix if trimmed.

    Args:
        s: The input string.
        max_len: Maximum length of the result.
        suffix: String to append when truncated.

    Returns:
        The truncated string.

    Raises:
        ValueError: If max_len is negative.

    Examples:
        >>> truncate("Hello World", 8)
        'Hello...'
        >>> truncate("Hi", 10)
        'Hi'
    """
    if max_len < 0:
        raise ValueError("max_len must be non-negative")
    if len(s) <= max_len:
        return s
    if max_len <= len(suffix):
        return suffix[:max_len]
    return s[: max_len - len(suffix)] + suffix


def slugify(s: str) -> str:
    """Convert a string to a URL-friendly slug.

    Lowercases, replaces spaces/underscores with hyphens, removes special chars.

    Args:
        s: The input string.

    Returns:
        A URL-safe slug.

    Examples:
        >>> slugify("Hello World! This is a Test")
        'hello-world-this-is-a-test'
    """
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def is_palindrome(s: str) -> bool:
    """Check if a string is a palindrome (case-insensitive, ignoring non-alphanumeric).

    Args:
        s: The input string.

    Returns:
        True if the string is a palindrome.

    Examples:
        >>> is_palindrome("A man, a plan, a canal: Panama")
        True
        >>> is_palindrome("hello")
        False
    """
    cleaned = re.sub(r"[^a-z0-9]", "", s.lower())
    return cleaned == cleaned[::-1]

"""Time utility functions for common datetime operations."""

from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    """Return current UTC time as an ISO 8601 string.

    Returns:
        ISO 8601 formatted string with timezone info.

    Examples:
        >>> now_iso()  # '2026-05-17T12:00:00+00:00'
    """
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 string into a datetime object.

    Args:
        s: An ISO 8601 formatted datetime string.

    Returns:
        A datetime object.

    Raises:
        ValueError: If the string is not a valid ISO format.

    Examples:
        >>> parse_iso("2026-05-17T12:00:00+00:00")
        datetime.datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    """
    return datetime.fromisoformat(s)


def humanize_duration(seconds: int | float) -> str:
    """Convert a duration in seconds to a human-readable string like '2h 30m 10s'.

    Args:
        seconds: Duration in seconds (must be non-negative).

    Returns:
        Human-readable duration string.

    Raises:
        ValueError: If seconds is negative.

    Examples:
        >>> humanize_duration(3661)
        '1h 1m 1s'
        >>> humanize_duration(45)
        '45s'
        >>> humanize_duration(0)
        '0s'
    """
    seconds = int(seconds)
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"

    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)

    parts: list[str] = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def is_business_hours(dt: datetime) -> bool:
    """Check if a datetime falls within business hours (Mon-Fri 9:00-17:00).

    Args:
        dt: The datetime to check.

    Returns:
        True if dt is on a weekday between 9:00 (inclusive) and 17:00 (exclusive).

    Examples:
        >>> from datetime import datetime
        >>> is_business_hours(datetime(2026, 5, 18, 10, 30))  # Monday 10:30
        True
        >>> is_business_hours(datetime(2026, 5, 17, 10, 30))  # Sunday 10:30
        False
    """
    return dt.weekday() < 5 and 9 <= dt.hour < 17


def days_ago(n: int) -> datetime:
    """Return a datetime representing n days ago from now (UTC).

    Args:
        n: Number of days in the past.

    Returns:
        A timezone-aware datetime in UTC.

    Examples:
        >>> days_ago(0)  # approximately now
        >>> days_ago(7)  # approximately 7 days ago
    """
    return datetime.now(timezone.utc) - timedelta(days=n)

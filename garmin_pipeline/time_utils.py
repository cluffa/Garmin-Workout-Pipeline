"""Time and date utilities."""

from datetime import datetime, timedelta


def parse_time_range(period: str) -> tuple[datetime, datetime]:
    """Parse a time period string into start and end datetimes.

    Supports: "7d", "30d", "90d", "ytd", "this-month", "YYYY-MM-DD:YYYY-MM-DD"
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if period.endswith("d"):
        days = int(period[:-1])
        return today - timedelta(days=days), today

    if period == "ytd":
        return datetime(today.year, 1, 1), today

    if period == "this-month":
        return datetime(today.year, today.month, 1), today

    if ":" in period:
        start_str, end_str = period.split(":", 1)
        start_date = datetime.strptime(start_str.strip(), "%Y-%m-%d")
        end_date = datetime.strptime(end_str.strip(), "%Y-%m-%d")
        if start_date > end_date:
            raise ValueError("Start date must be before or equal to end date")
        return start_date, end_date

    raise ValueError(
        f"Invalid period: {period}. Use '7d', '30d', '90d', 'ytd', 'YYYY-MM-DD:YYYY-MM-DD'"
    )


def get_range_description(period: str) -> str:
    """Human-readable description of a time period."""
    try:
        start_date, end_date = parse_time_range(period)
        days = (end_date - start_date).days + 1
        if period.endswith("d"):
            return f"Last {period[:-1]} days"
        if period == "ytd":
            return f"Year to date ({start_date.year})"
        if period == "this-month":
            return start_date.strftime("%B %Y")
        return f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')} ({days} days)"
    except ValueError:
        return period


def format_date_for_api(dt: datetime) -> str:
    """Format a datetime for Garmin API (YYYY-MM-DD)."""
    return dt.strftime("%Y-%m-%d")


def parse_date_string(date_str: str) -> datetime:
    """Parse 'today', 'yesterday', or 'YYYY-MM-DD' to datetime."""
    date_str = date_str.strip().lower()
    if date_str == "today":
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if date_str == "yesterday":
        yesterday = datetime.now() - timedelta(days=1)
        return yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(
            f"Invalid date: {date_str}. Use 'today', 'yesterday', or 'YYYY-MM-DD'"
        ) from e

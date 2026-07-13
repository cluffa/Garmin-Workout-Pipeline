"""Calendar and event tools for Garmin Connect."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.tools._format import error_json, to_json


def query_calendar_events(
    start_date: str | None = None,
    end_date: str | None = None,
    days_ahead: int = 90,
) -> str:
    """Query upcoming Garmin calendar events (races, scheduled workouts, etc.).

    Searches your Garmin calendar for:
    - Races and events you've registered for (via Garmin's event system)
    - Scheduled workouts with dates
    - All-day events

    Args:
        start_date: Range start (YYYY-MM-DD). Defaults to today.
        end_date: Range end (YYYY-MM-DD). Defaults to start_date + days_ahead.
        days_ahead: Days to look ahead from start_date (default: 90).
    """
    try:
        client = get_client()

        if not start_date:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = datetime.strptime(start_date, "%Y-%m-%d")

        if not end_date:
            end = start + timedelta(days=days_ahead)
        else:
            end = datetime.strptime(end_date, "%Y-%m-%d")

        events: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Query scheduled workouts for each month in range
        current = datetime(start.year, start.month, 1)
        while current <= end:
            try:
                sw = client.safe_call("get_scheduled_workouts", current.year, current.month)
                if isinstance(sw, dict) and "calendarItems" in sw:
                    range_start = start.strftime("%Y-%m-%d")
                    range_end = end.strftime("%Y-%m-%d")
                    for item in sw["calendarItems"]:
                        item_date = item.get("date", "")
                        if item_date and range_start <= item_date[:10] <= range_end:
                            key = f"{item_date[:10]}:{item.get('title', '')}"
                            if key in seen:
                                continue
                            seen.add(key)

                            item_type = item.get("itemType", "unknown")
                            is_race = item.get("isRace", False)

                            event = {
                                "date": item_date[:10],
                                "name": item.get("title", "Unnamed"),
                                "type": "race" if is_race else item_type,
                                "is_race": is_race,
                                "sport_type_id": item.get("activityTypeId"),
                                "url": item.get("url"),
                            }
                            if item.get("distance"):
                                event["distance_meters"] = item["distance"]
                            if item.get("duration"):
                                event["duration_seconds"] = item["duration"]
                            events.append(event)
            except Exception:
                pass
            # Next month
            if current.month == 12:
                current = datetime(current.year + 1, 1, 1)
            else:
                current = datetime(current.year, current.month + 1, 1)

        # Also check all-day events for each day (up to 60 days to avoid rate limits)
        check_days = min((end - start).days + 1, 60)
        for i in range(check_days):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            try:
                day_events = client.safe_call("get_all_day_events", d)
                if isinstance(day_events, list):
                    for ev in day_events:
                        if isinstance(ev, dict):
                            events.append({
                                "date": d,
                                "name": ev.get("eventName", ev.get("title", "Event")),
                                "type": "all_day_event",
                            })
            except Exception:
                pass

        # Sort by date
        events.sort(key=lambda e: e.get("date", "9999"))

        # Count by type (races are flagged inline via is_race — not duplicated)
        races = [e for e in events if e.get("is_race")]
        workouts = [e for e in events if e.get("type") in ("workout", "scheduled_workout")]
        other = [e for e in events if e not in races and e not in workouts]

        insights = [f"{len(events)} upcoming event(s)"]
        if races:
            insights.append(f"  {len(races)} race(s): {', '.join(r['name'] for r in races)}")
        if workouts:
            insights.append(f"  {len(workouts)} scheduled workout(s)")
        if other:
            insights.append(f"  {len(other)} other event(s)")

        return to_json({
            "data": {
                "events": events,
                "count": len(events),
                "range": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
            },
            "analysis": {"insights": insights},
        })

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))

"""Calendar and event tools for Garmin Connect."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client


def query_calendar_events(
    start_date: str | None = None,
    end_date: str | None = None,
    days_ahead: int = 90,
) -> str:
    """Query upcoming Garmin calendar events (races, scheduled workouts, etc.).

    Searches for events in your Garmin calendar including:
    - Races and events you've registered for
    - Scheduled workouts
    - Training plan events

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

        # Try getting calendar events
        try:
            # Garmin API exposes scheduled workouts through get_workouts
            workouts = client.safe_call("get_workouts")
            if isinstance(workouts, list):
                for w in workouts:
                    # Check if workout has a scheduled date
                    schedule_date = w.get("scheduledDate") or w.get("scheduleDate")
                    if schedule_date:
                        if isinstance(schedule_date, str):
                            try:
                                sd = datetime.fromisoformat(schedule_date.replace("Z", "+00:00"))
                                schedule_date = sd.strftime("%Y-%m-%d")
                            except Exception:
                                pass
                        if start.strftime("%Y-%m-%d") <= str(schedule_date)[:10] <= end.strftime("%Y-%m-%d"):
                            events.append({
                                "type": "scheduled_workout",
                                "name": w.get("workoutName", "Unnamed"),
                                "date": str(schedule_date)[:10],
                                "workout_id": w.get("workoutId"),
                                "sport": w.get("sportType", {}).get("sportTypeKey", "unknown"),
                            })
        except Exception:
            pass

        # Try getting training plan events
        try:
            plan_events = client.safe_call("get_training_plan_calendar", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            if isinstance(plan_events, list):
                for e in plan_events:
                    events.append({
                        "type": "training_plan",
                        "name": e.get("workoutName", e.get("name", "Training")),
                        "date": e.get("date", e.get("scheduledDate", ""))[:10] if e.get("date") or e.get("scheduledDate") else "",
                        "sport": e.get("sportType", {}).get("sportTypeKey", "unknown"),
                    })
        except Exception:
            pass

        # Try getting registered events/races
        try:
            # Some Garmin accounts have event registrations accessible
            races = client.safe_call("get_events", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            if isinstance(races, list):
                for r in races:
                    events.append({
                        "type": "race",
                        "name": r.get("eventName", r.get("name", "Event")),
                        "date": r.get("eventDate", r.get("date", ""))[:10] if r.get("eventDate") or r.get("date") else "",
                        "distance": r.get("distance"),
                        "location": r.get("location", r.get("city", "")),
                    })
        except Exception:
            pass

        # Try personal events from calendar
        try:
            personal = client.safe_call("get_personal_events", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            if isinstance(personal, list):
                for p in personal:
                    events.append({
                        "type": "personal_event",
                        "name": p.get("eventName", p.get("name", "Event")),
                        "date": p.get("eventDate", p.get("date", ""))[:10] if p.get("eventDate") or p.get("date") else "",
                    })
        except Exception:
            pass

        # Sort by date
        events.sort(key=lambda e: e.get("date", "9999"))

        # Count by type
        type_counts: dict[str, int] = {}
        for e in events:
            t = e["type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        insights = [f"{len(events)} upcoming event(s) in range"]
        for t, c in type_counts.items():
            insights.append(f"  {t}: {c}")

        return json.dumps({
            "data": {
                "events": events,
                "count": len(events),
                "range": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
            },
            "analysis": {"insights": insights},
            "metadata": {"fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})

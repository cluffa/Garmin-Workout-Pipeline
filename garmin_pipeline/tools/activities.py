"""Activity query tools."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.time_utils import parse_date_string


def _dist(m: float, unit: str) -> str:
    if unit == "imperial":
        return f"{m / 1609.34:.2f} mi"
    return f"{m / 1000:.2f} km"


def _dur(s: float) -> str:
    h, m = divmod(int(s), 3600)
    m, sec = divmod(m, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _pace(mps: float, unit: str) -> str:
    if mps == 0:
        return "N/A"
    if unit == "imperial":
        spm = 1609.34 / mps
        return f"{int(spm // 60)}:{int(spm % 60):02d} /mi"
    spk = 1000 / mps
    return f"{int(spk // 60)}:{int(spk % 60):02d} /km"


def _speed(mps: float, unit: str) -> str:
    if unit == "imperial":
        return f"{mps * 2.23694:.2f} mph"
    return f"{mps * 3.6:.2f} km/h"


def _format_activity(act: dict, unit: str = "metric") -> dict:
    """Enrich an activity dict with formatted fields."""
    a = dict(act)
    if a.get("distance"):
        a["distance"] = {"meters": a["distance"], "formatted": _dist(a["distance"], unit)}
    if a.get("duration"):
        a["duration"] = {"seconds": a["duration"], "formatted": _dur(a["duration"])}
    if a.get("elevationGain"):
        a["elevationGain"] = {"meters": a["elevationGain"],
            "formatted": f"{a['elevationGain'] * 3.28084:.0f} ft" if unit == "imperial" else f"{a['elevationGain']:.0f} m"}
    if a.get("averageSpeed"):
        a["averageSpeed"] = {"mps": a["averageSpeed"],
            "formatted_speed": _speed(a["averageSpeed"], unit),
            "formatted_pace": _pace(a["averageSpeed"], unit)}
    if a.get("startTimeLocal") and isinstance(a["startTimeLocal"], str):
        try:
            dt = datetime.fromisoformat(a["startTimeLocal"].replace("Z", "+00:00"))
            a["startTimeLocal"] = {"datetime": a["startTimeLocal"], "date": dt.strftime("%Y-%m-%d"),
                "day_of_week": dt.strftime("%A"), "formatted": dt.strftime("%A, %B %d, %Y at %I:%M %p")}
        except Exception:
            pass
    if a.get("startTimeGMT") and isinstance(a["startTimeGMT"], str):
        try:
            dt = datetime.fromisoformat(a["startTimeGMT"].replace("Z", "+00:00"))
            a["startTimeGMT"] = {"datetime": a["startTimeGMT"], "date": dt.strftime("%Y-%m-%d"),
                "day_of_week": dt.strftime("%A"), "formatted": dt.strftime("%A, %B %d, %Y at %I:%M %p")}
        except Exception:
            pass
    if a.get("averageHR"):
        a["heart_rate"] = {"avg_bpm": round(a["averageHR"])}
    if a.get("maxHR"):
        a.setdefault("heart_rate", {})["max_bpm"] = round(a["maxHR"])
    if a.get("avgPower"):
        a["power"] = {"avg_watts": round(a["avgPower"])}
    if a.get("maxPower"):
        a.setdefault("power", {})["max_watts"] = round(a["maxPower"])
    return a


def query_activities(
    activity_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    date: str | None = None,
    cursor: str | None = None,
    limit: str | int | None = None,
    activity_type: str = "",
    unit: str = "metric",
) -> str:
    """Query activities with flexible parameters and pagination support.

    This unified tool supports multiple query patterns:
    1. Get specific activity: provide activity_id
    2. Get activities by date range: provide start_date and end_date (paginated)
    3. Get activities for specific date: provide date
    4. Get paginated activities: use cursor and limit
    5. Get last activity: no parameters

    All queries can be filtered by activity_type (e.g., 'running', 'cycling').

    Pagination:
    For large time ranges, use pagination to retrieve all activities:
    1. Make initial request without cursor
    2. Check response["pagination"]["has_more"]
    3. Use response["pagination"]["cursor"] for next page

    Returns: JSON string with structure:
    {
        "data": {
            "activity": {...}       // Single activity mode
            OR
            "activities": [...],    // List mode
            "count": N
        },
        "pagination": {             // List mode only (when paginated)
            "cursor": "...",
            "has_more": true,
            "limit": 20,
            "returned": 20
        },
        "metadata": {...}
    }
    """
    try:
        client = get_client()

        # Coerce limit
        if limit is not None:
            if isinstance(limit, str):
                limit = int(limit)
            limit = max(1, min(int(limit), 50))
        else:
            limit = 10

        # Single activity by ID
        if activity_id is not None:
            activity = client.safe_call("get_activity", activity_id)
            formatted = _format_activity(activity, unit)
            return json.dumps({
                "data": {"activity": formatted},
                "metadata": {"query_type": "single_activity", "activity_id": activity_id,
                    "unit": unit, "fetched_at": datetime.now().isoformat()}
            })

        # Determine date range
        if date:
            dt = parse_date_string(date)
            start = dt.strftime("%Y-%m-%d")
            end = dt.strftime("%Y-%m-%d")
        elif start_date and end_date:
            start = start_date
            end = end_date
        elif start_date:
            start = start_date
            end = datetime.now().strftime("%Y-%m-%d")
        else:
            # Default: last 7 days
            from datetime import timedelta
            start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            end = datetime.now().strftime("%Y-%m-%d")

        # Parse cursor for page number
        current_page = 1
        if cursor:
            try:
                import base64
                decoded = json.loads(base64.b64decode(cursor).decode())
                current_page = decoded.get("page", 1)
            except Exception:
                pass

        # Fetch activities
        all_activities = client.safe_call("get_activities_by_date", start, end, activity_type)
        if not all_activities:
            return json.dumps({
                "data": {"activities": [], "count": 0},
                "pagination": {"cursor": None, "has_more": False, "limit": limit, "returned": 0},
                "metadata": {"query_type": "activity_list", "start_date": start, "end_date": end,
                    "activity_type": activity_type, "unit": unit, "fetched_at": datetime.now().isoformat()}
            })

        # Slice for pagination
        offset = (current_page - 1) * limit
        page = all_activities[offset:offset + limit + 1]
        has_more = len(page) > limit
        page = page[:limit]

        formatted = [_format_activity(a, unit) for a in page]

        # Build next cursor
        next_cursor = None
        if has_more:
            import base64
            next_cursor = base64.b64encode(
                json.dumps({"page": current_page + 1}).encode()
            ).decode()

        return json.dumps({
            "data": {"activities": formatted, "count": len(formatted),
                "aggregated": _aggregate(page, unit)},
            "pagination": {"cursor": next_cursor, "has_more": has_more,
                "limit": limit, "returned": len(formatted)},
            "metadata": {"query_type": "activity_list", "start_date": start, "end_date": end,
                "activity_type": activity_type, "unit": unit, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message,
            "timestamp": datetime.now().isoformat()}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e),
            "timestamp": datetime.now().isoformat()}})


def _aggregate(activities: list, unit: str) -> dict:
    """Aggregate metrics from a list of activities."""
    if not activities:
        return {}
    total_d = sum(a.get("distance", 0) or 0 for a in activities)
    total_t = sum(a.get("duration", 0) or 0 for a in activities)
    total_e = sum(a.get("elevationGain", 0) or 0 for a in activities)
    return {
        "count": len(activities),
        "total_distance": {"meters": total_d, "formatted": _dist(total_d, unit)},
        "total_time": {"seconds": total_t, "formatted": _dur(total_t)},
        "total_elevation": {"meters": total_e,
            "formatted": f"{total_e * 3.28084:.0f} ft" if unit == "imperial" else f"{total_e:.0f} m"},
    }


def get_activity_details(
    activity_id: int,
    include_splits: bool = True,
    include_weather: bool = True,
    include_hr_zones: bool = True,
    include_gear: bool = True,
    include_exercise_sets: bool = False,
    unit: str = "metric",
) -> str:
    """Get comprehensive details for a specific activity.

    Fetch exactly the information you need about an activity with flexible
    detail options.

    By default, includes splits, weather, HR zones, and gear. Exercise sets
    are only included when explicitly requested (useful for strength training).

    When include_splits=True and the activity has only 1 lap, estimated km/mile
    splits will be computed based on average pace.
    """
    try:
        client = get_client()
        activity = client.safe_call("get_activity", activity_id)
        result = {"activityId": activity_id,
            "activityUUID": {"uuid": activity.get("activityUUID", {}).get("uuid", "")},
            "activityName": activity.get("activityName", ""),
            "activityTypeDTO": activity.get("activityTypeDTO", {}),
            "eventTypeDTO": activity.get("eventTypeDTO", {}),
            "summaryDTO": activity.get("summaryDTO", {}),
            "locationName": activity.get("locationName", ""),
        }

        if include_splits:
            result["splitSummaries"] = activity.get("splitSummaries", [])
        if include_weather:
            result["weatherDTO"] = activity.get("weatherDTO")
        if include_hr_zones:
            result["hrTimeInZones"] = activity.get("hrTimeInZones", [])
        if include_gear:
            result["gearDTO"] = activity.get("gearDTO")

        if include_exercise_sets:
            detail = client.safe_call("get_activity_exercise_sets", activity_id)
            result["exerciseSets"] = detail

        return json.dumps({
            "data": {"activity": result},
            "metadata": {"query_type": "activity_details", "activity_id": activity_id,
                "unit": unit, "includes": {"splits": include_splits, "weather": include_weather,
                    "hr_zones": include_hr_zones, "gear": include_gear,
                    "exercise_sets": include_exercise_sets},
                "fetched_at": datetime.now().isoformat()}
        })
    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message,
            "timestamp": datetime.now().isoformat()}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e),
            "timestamp": datetime.now().isoformat()}})

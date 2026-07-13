"""Activity query tools."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.time_utils import parse_date_string
from garmin_pipeline.tools._format import (
    error_json,
    fmt_dist,
    fmt_dur,
    fmt_elev,
    project_activity,
    to_json,
)


def _decode_page(cursor: str | None) -> int:
    if not cursor:
        return 1
    try:
        return json.loads(base64.b64decode(cursor).decode()).get("page", 1)
    except Exception:
        return 1


def _encode_page(page: int) -> str:
    return base64.b64encode(json.dumps({"page": page}).encode()).decode()


def query_activities(
    activity_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    date: str | None = None,
    cursor: str | None = None,
    limit: str | int | None = None,
    activity_type: str = "",
    unit: str = "metric",
    raw: bool = False,
) -> str:
    """Query activities: by ID, date range, single date, or last 7 days (default).

    Optionally filter by activity_type (e.g. 'running', 'cycling'). Results are
    paginated: pass pagination.cursor back to get the next page while
    pagination.has_more is true.

    Returns compact summaries (distance, duration, pace, HR, power, training
    effect, ...). Set raw=true for complete unabridged Garmin payloads (large).
    """
    try:
        client = get_client()

        if limit is not None:
            if isinstance(limit, str):
                limit = int(limit)
            limit = max(1, min(int(limit), 50))
        else:
            limit = 10

        # Single activity by ID
        if activity_id is not None:
            activity = client.safe_call("get_activity", activity_id)
            formatted = activity if raw else project_activity(activity, unit)
            return to_json({"data": {"activity": formatted}})

        # Determine date range
        if date:
            dt = parse_date_string(date)
            start = end = dt.strftime("%Y-%m-%d")
        elif start_date and end_date:
            start, end = start_date, end_date
        elif start_date:
            start = start_date
            end = datetime.now().strftime("%Y-%m-%d")
        else:
            start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            end = datetime.now().strftime("%Y-%m-%d")

        current_page = _decode_page(cursor)

        all_activities = client.safe_call("get_activities_by_date", start, end, activity_type)
        if not all_activities:
            return to_json({
                "data": {"activities": [], "count": 0,
                    "range": {"start": start, "end": end}},
                "pagination": {"has_more": False},
            })

        offset = (current_page - 1) * limit
        page = all_activities[offset:offset + limit + 1]
        has_more = len(page) > limit
        page = page[:limit]

        formatted = page if raw else [project_activity(a, unit) for a in page]

        return to_json({
            "data": {"activities": formatted, "count": len(formatted),
                "range": {"start": start, "end": end},
                "aggregated": _aggregate(page, unit)},
            "pagination": {"cursor": _encode_page(current_page + 1) if has_more else None,
                "has_more": has_more},
        })

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


def _aggregate(activities: list, unit: str) -> dict:
    """Aggregate metrics from a list of activities."""
    if not activities:
        return {}
    total_d = sum(a.get("distance", 0) or 0 for a in activities)
    total_t = sum(a.get("duration", 0) or 0 for a in activities)
    total_e = sum(a.get("elevationGain", 0) or 0 for a in activities)
    return {
        "count": len(activities),
        "total_distance": fmt_dist(total_d, unit),
        "total_time": fmt_dur(total_t),
        "total_elevation": fmt_elev(total_e, unit),
    }


def get_activity_details(
    activity_id: int,
    include_splits: bool = True,
    include_weather: bool = True,
    include_hr_zones: bool = True,
    include_gear: bool = True,
    include_exercise_sets: bool = False,
    unit: str = "metric",
    raw: bool = False,
) -> str:
    """Get comprehensive details for a specific activity.

    By default includes summary, splits, weather, HR zones, and gear. Set
    include_exercise_sets=true for strength training sets. Set raw=true to get
    the complete unabridged Garmin activity payload (large).
    """
    try:
        client = get_client()
        activity = client.safe_call("get_activity", activity_id)

        if raw:
            result = activity
            if include_exercise_sets:
                result["exerciseSets"] = client.safe_call(
                    "get_activity_exercise_sets", activity_id)
            return to_json({"data": {"activity": result}})

        result = {"activityId": activity_id,
            "activityName": activity.get("activityName", ""),
            "activityType": activity.get("activityTypeDTO", {}).get("typeKey"),
            "eventType": activity.get("eventTypeDTO", {}).get("typeKey"),
            "summary": activity.get("summaryDTO", {}),
            "locationName": activity.get("locationName", ""),
        }

        if include_splits:
            result["splitSummaries"] = activity.get("splitSummaries", [])
        if include_weather:
            result["weather"] = activity.get("weatherDTO")
        if include_hr_zones:
            result["hrTimeInZones"] = activity.get("hrTimeInZones", [])
        if include_gear:
            result["gear"] = activity.get("gearDTO")

        if include_exercise_sets:
            result["exerciseSets"] = client.safe_call(
                "get_activity_exercise_sets", activity_id)

        return to_json({"data": {"activity": result}})
    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))

"""Training analysis and performance tools."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.time_utils import format_date_for_api, get_range_description, parse_time_range
from garmin_pipeline.tools.activities import _dist, _dur, _format_activity


def analyze_training_period(
    period: str = "30d",
    activity_type: str = "",
    unit: str = "metric",
) -> str:
    """Analyze training over a specified period with comprehensive insights.

    Provides:
    - Total volume (activities, distance, time, elevation)
    - Activity type breakdown
    - Weekly trends
    - Performance insights

    Example periods: "7d", "30d", "90d", "ytd", "this-month", "YYYY-MM-DD:YYYY-MM-DD"
    """
    try:
        client = get_client()
        start_date, end_date = parse_time_range(period)
        start_str = format_date_for_api(start_date)
        end_str = format_date_for_api(end_date)
        period_desc = get_range_description(period)
        total_days = (end_date - start_date).days + 1

        activities = client.safe_call("get_activities_by_date", start_str, end_str, activity_type)

        if not activities:
            return json.dumps({
                "data": {"period": {"description": period_desc, "start_date": start_str,
                    "end_date": end_str, "days": total_days},
                    "summary": {"total_activities": 0}},
                "analysis": {"insights": ["No activities found in this period"]},
                "metadata": {"period": period, "activity_type": activity_type or "all",
                    "unit": unit, "fetched_at": datetime.now().isoformat()}
            })

        total_dist = sum(a.get("distance", 0) or 0 for a in activities)
        total_time = sum(a.get("duration", 0) or 0 for a in activities)
        total_elev = sum(a.get("elevationGain", 0) or 0 for a in activities)

        # By activity type
        by_type: dict[str, dict] = defaultdict(lambda: {"count": 0, "distance": 0, "time": 0})
        for a in activities:
            t = a.get("activityType", {}).get("typeKey", "unknown")
            by_type[t]["count"] += 1
            by_type[t]["distance"] += a.get("distance", 0) or 0
            by_type[t]["time"] += a.get("duration", 0) or 0

        summary = {
            "total_activities": len(activities),
            "total_distance": {"meters": total_dist, "formatted": _dist(total_dist, unit)},
            "total_time": {"seconds": total_time, "formatted": _dur(total_time)},
            "total_elevation": {"meters": total_elev,
                "formatted": f"{total_elev:.0f} m"},
            "averages": {
                "distance_per_activity": {"meters": total_dist / len(activities),
                    "formatted": _dist(total_dist / len(activities), unit)},
                "activities_per_week": round(len(activities) / (total_days / 7), 1),
            },
        }

        # Weekly trends
        weeks: list[dict] = []
        current = start_date
        while current <= end_date:
            week_end = min(current + timedelta(days=6), end_date)
            week_acts = [a for a in activities
                if _act_in_range(a, current.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d"))]
            if week_acts or current <= end_date - timedelta(days=6):
                w = {
                    "week_start": {"datetime": current.isoformat(), "date": current.strftime("%Y-%m-%d"),
                        "day_of_week": current.strftime("%A"),
                        "formatted": current.strftime("%A, %B %d, %Y at 12:00 AM")},
                    "week_end": {"datetime": week_end.isoformat(), "date": week_end.strftime("%Y-%m-%d"),
                        "day_of_week": week_end.strftime("%A"),
                        "formatted": week_end.strftime("%A, %B %d, %Y at 12:00 AM")},
                    "activities": len(week_acts),
                    "distance": {"meters": sum(a.get("distance", 0) or 0 for a in week_acts),
                        "formatted": _dist(sum(a.get("distance", 0) or 0 for a in week_acts), unit)},
                    "time": {"seconds": sum(a.get("duration", 0) or 0 for a in week_acts),
                        "formatted": _dur(sum(a.get("duration", 0) or 0 for a in week_acts))},
                }
                weeks.append(w)
            current += timedelta(days=7)

        # Type breakdown
        type_breakdown = []
        for t, stats in sorted(by_type.items(), key=lambda x: x[1]["distance"], reverse=True):
            pct = stats["count"] / len(activities) * 100
            type_breakdown.append({
                "type": t, "count": stats["count"], "percentage": round(pct, 1),
                "distance": {"meters": stats["distance"], "formatted": _dist(stats["distance"], unit)},
                "time": {"seconds": stats["time"], "formatted": _dur(stats["time"])},
            })

        # Insights
        insights = [f"High training volume: {len(activities)} activities in {total_days} days"]
        if len(weeks) >= 2:
            first_wk = weeks[0]["distance"]["meters"]
            last_wk = weeks[-2]["distance"]["meters"] if weeks[-1]["activities"] == 0 else weeks[-1]["distance"]["meters"]
            if last_wk > first_wk * 1.05:
                insights.append("Training volume increasing over time")
            elif last_wk < first_wk * 0.95:
                insights.append("Training volume decreasing over time")
        dominant = max(by_type, key=lambda t: by_type[t]["count"])
        insights.append(f"Training primarily focused on {dominant}")

        return json.dumps({
            "data": {
                "period": {"description": period_desc, "start_date": start_str,
                    "end_date": end_str, "days": total_days},
                "summary": summary,
                "by_activity_type": type_breakdown,
                "trends": {"weekly": weeks},
            },
            "analysis": {"insights": insights},
            "metadata": {"period": period, "activity_type": activity_type or "all",
                "unit": unit, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})


def _act_in_range(act: dict, start: str, end: str) -> bool:
    """Check if an activity falls within a date range based on its startTimeLocal."""
    st = act.get("startTimeLocal")
    if not st:
        return False
    try:
        d = st[:10]  # YYYY-MM-DD
        return start <= d <= end
    except Exception:
        return False


def get_performance_metrics(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_vo2_max: bool = True,
    include_hill_score: bool = True,
    include_endurance_score: bool = True,
    include_hrv: bool = True,
    include_fitness_age: bool = True,
) -> str:
    """Get comprehensive performance metrics.

    Includes VO2 max, hill score, endurance score, heart rate variability,
    and fitness age data.
    """
    try:
        client = get_client()
        result: dict[str, Any] = {}

        if include_vo2_max:
            try:
                result["vo2_max"] = client.safe_call("get_vo2_max_data", datetime.now().strftime("%Y-%m-%d"))
            except Exception:
                result["vo2_max"] = []

        if include_hill_score:
            try:
                result["hill_score"] = client.safe_call("get_hill_score")
            except Exception:
                result["hill_score"] = None

        if include_endurance_score:
            try:
                result["endurance_score"] = client.safe_call("get_endurance_score")
            except Exception:
                result["endurance_score"] = None

        if include_hrv:
            try:
                result["hrv"] = client.safe_call("get_hrv_data", datetime.now().strftime("%Y-%m-%d"))
            except Exception:
                result["hrv"] = None

        if include_fitness_age:
            try:
                result["fitness_age"] = client.safe_call("get_fitness_age")
            except Exception:
                result["fitness_age"] = None

        insights = [f"Available performance metrics: {', '.join(k for k, v in result.items() if v)}"]

        return json.dumps({
            "data": result,
            "analysis": {"insights": insights},
            "metadata": {"date": date or datetime.now().strftime("%Y-%m-%d"),
                "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})


def get_training_effect(
    activity_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    metric: str = "distance",
) -> str:
    """Get training effect and progress summary.

    Supports:
    1. Training effect for specific activity (provide activity_id)
    2. Progress summary over date range (provide start_date, end_date, metric)
    """
    try:
        client = get_client()

        if activity_id is not None:
            activity = client.safe_call("get_activity", activity_id)
            te = {
                "aerobic": activity.get("aerobicTrainingEffect"),
                "anaerobic": activity.get("anaerobicTrainingEffect"),
                "load": activity.get("activityTrainingLoad"),
                "label": activity.get("trainingEffectLabel"),
            }
            return json.dumps({
                "data": {"training_effect": te, "activity_id": activity_id},
                "metadata": {"fetched_at": datetime.now().isoformat()}
            })

        if start_date and end_date:
            activities = client.safe_call("get_activities_by_date", start_date, end_date, "")
            progress = [{"date": a.get("startTimeLocal", "")[:10],
                "distance": a.get("distance"), "duration": a.get("duration"),
                "training_load": a.get("activityTrainingLoad")}
                for a in activities[:50]]
            return json.dumps({
                "data": {"progress": progress, "count": len(progress)},
                "metadata": {"start_date": start_date, "end_date": end_date,
                    "metric": metric, "fetched_at": datetime.now().isoformat()}
            })

        return json.dumps({"error": {"type": "invalid_parameters",
            "message": "Provide activity_id or (start_date + end_date)"}})

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})


def compare_activities(
    activity_ids: str,
    unit: str = "metric",
) -> str:
    """Compare multiple activities side-by-side.

    Analyzes 2-5 activities and provides:
    - Side-by-side metrics comparison
    - Identification of best/worst performances
    - Performance insights and patterns

    Example: activity_ids="12345678,12345679,12345680"
    """
    try:
        client = get_client()
        ids = [int(i.strip()) for i in activity_ids.split(",") if i.strip()]
        if len(ids) < 2 or len(ids) > 5:
            return json.dumps({"error": {"type": "invalid_parameters",
                "message": "Provide 2-5 comma-separated activity IDs"}})

        acts = []
        for aid in ids:
            try:
                a = client.safe_call("get_activity", aid)
                acts.append(_format_activity(a, unit))
            except Exception:
                acts.append({"activityId": aid, "error": "Not found"})

        return json.dumps({
            "data": {"activities": acts, "count": len(acts)},
            "metadata": {"unit": unit, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})

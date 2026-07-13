"""Training analysis and performance tools."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.time_utils import format_date_for_api, get_range_description, parse_time_range
from garmin_pipeline.tools._format import (
    error_json,
    fmt_dist,
    fmt_dur,
    fmt_elev,
    project_activity,
    to_json,
)


def analyze_training_period(
    period: str = "30d",
    activity_type: str = "",
    unit: str = "metric",
) -> str:
    """Analyze training over a period: total volume, activity type breakdown,
    weekly trends, and insights.

    Example periods: "7d", "30d", "90d", "ytd", "this-month", "YYYY-MM-DD:YYYY-MM-DD".
    Optionally filter by activity_type (e.g. 'running').
    """
    try:
        client = get_client()
        start_date, end_date = parse_time_range(period)
        start_str = format_date_for_api(start_date)
        end_str = format_date_for_api(end_date)
        period_desc = get_range_description(period)
        total_days = (end_date - start_date).days + 1

        activities = client.safe_call("get_activities_by_date", start_str, end_str, activity_type)

        period_info = {"description": period_desc, "start_date": start_str,
            "end_date": end_str, "days": total_days}

        if not activities:
            return to_json({
                "data": {"period": period_info, "summary": {"total_activities": 0}},
                "analysis": {"insights": ["No activities found in this period"]},
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
            "total_distance_m": round(total_dist),
            "total_distance": fmt_dist(total_dist, unit),
            "total_time_s": round(total_time),
            "total_time": fmt_dur(total_time),
            "total_elevation": fmt_elev(total_elev, unit),
            "avg_distance_per_activity": fmt_dist(total_dist / len(activities), unit),
            "activities_per_week": round(len(activities) / (total_days / 7), 1),
        }

        # Weekly trends
        weeks: list[dict] = []
        current = start_date
        while current <= end_date:
            week_end = min(current + timedelta(days=6), end_date)
            week_acts = [a for a in activities
                if _act_in_range(a, current.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d"))]
            if week_acts or current <= end_date - timedelta(days=6):
                dist = sum(a.get("distance", 0) or 0 for a in week_acts)
                dur = sum(a.get("duration", 0) or 0 for a in week_acts)
                weeks.append({
                    "week_start": current.strftime("%Y-%m-%d"),
                    "week_end": week_end.strftime("%Y-%m-%d"),
                    "activities": len(week_acts),
                    "distance_m": round(dist),
                    "distance": fmt_dist(dist, unit),
                    "time": fmt_dur(dur),
                })
            current += timedelta(days=7)

        # Type breakdown
        type_breakdown = []
        for t, stats in sorted(by_type.items(), key=lambda x: x[1]["distance"], reverse=True):
            pct = stats["count"] / len(activities) * 100
            type_breakdown.append({
                "type": t, "count": stats["count"], "percentage": round(pct, 1),
                "distance": fmt_dist(stats["distance"], unit),
                "time": fmt_dur(stats["time"]),
            })

        # Insights
        insights = [f"{len(activities)} activities in {total_days} days"]
        if len(weeks) >= 2:
            first_wk = weeks[0]["distance_m"]
            last_full = weeks[-2] if weeks[-1]["activities"] == 0 else weeks[-1]
            last_wk = last_full["distance_m"]
            if last_wk > first_wk * 1.05:
                insights.append("Training volume increasing over time")
            elif last_wk < first_wk * 0.95:
                insights.append("Training volume decreasing over time")
        dominant = max(by_type, key=lambda t: by_type[t]["count"])
        insights.append(f"Training primarily focused on {dominant}")

        return to_json({
            "data": {
                "period": period_info,
                "summary": summary,
                "by_activity_type": type_breakdown,
                "trends": {"weekly": weeks},
            },
            "analysis": {"insights": insights},
        })

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


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
    raw: bool = False,
) -> str:
    """Get performance metrics: VO2 max (latest + history), hill score,
    endurance score, HRV summary, and fitness age.

    HRV per-reading data is omitted unless raw=true.
    """
    try:
        client = get_client()
        result: dict = {}

        if include_vo2_max:
            try:
                # VO2 max is embedded in activity data, not a standalone endpoint
                activities = client.safe_call("get_activities_by_date",
                    (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d"),
                    datetime.now().strftime("%Y-%m-%d"), "running")
                vo2_vals = []
                for a in (activities or [])[:50]:
                    if a.get("vO2MaxValue"):
                        vo2_vals.append({
                            "date": a.get("startTimeLocal", "")[:10],
                            "value": a["vO2MaxValue"],
                        })
                if vo2_vals:
                    latest = vo2_vals[0]
                    result["vo2_max"] = {
                        "latest": latest["value"],
                        "date": latest["date"],
                        "history": vo2_vals[:20],
                    }
                else:
                    result["vo2_max"] = None
            except Exception:
                result["vo2_max"] = None

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
                hrv = client.safe_call("get_hrv_data", datetime.now().strftime("%Y-%m-%d"))
                if not raw and isinstance(hrv, dict):
                    hrv = {k: v for k, v in hrv.items() if k != "hrvReadings"}
                result["hrv"] = hrv
            except Exception:
                result["hrv"] = None

        if include_fitness_age:
            try:
                result["fitness_age"] = client.safe_call("get_fitness_age")
            except Exception:
                result["fitness_age"] = None

        available = ", ".join(k for k, v in result.items() if v) or "none"
        return to_json({
            "data": result,
            "analysis": {"insights": [f"Available performance metrics: {available}"]},
        })

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


def get_training_effect(
    activity_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    metric: str = "distance",
) -> str:
    """Get training effect for an activity (provide activity_id), or a
    training-load progress list over a date range (provide start_date + end_date)."""
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
            return to_json({"data": {"training_effect": te, "activity_id": activity_id}})

        if start_date and end_date:
            activities = client.safe_call("get_activities_by_date", start_date, end_date, "")
            progress = [{"date": a.get("startTimeLocal", "")[:10],
                "distance_m": round(a["distance"]) if a.get("distance") else None,
                "duration_s": round(a["duration"]) if a.get("duration") else None,
                "training_load": a.get("activityTrainingLoad")}
                for a in activities[:50]]
            return to_json({"data": {"progress": progress, "count": len(progress)}})

        return error_json("invalid_parameters", "Provide activity_id or (start_date + end_date)")

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


def compare_activities(
    activity_ids: str,
    unit: str = "metric",
) -> str:
    """Compare 2-5 activities side-by-side using compact summaries (distance,
    duration, pace, HR, power, training effect).

    Example: activity_ids="12345678,12345679,12345680"
    """
    try:
        client = get_client()
        ids = [int(i.strip()) for i in activity_ids.split(",") if i.strip()]
        if len(ids) < 2 or len(ids) > 5:
            return error_json("invalid_parameters", "Provide 2-5 comma-separated activity IDs")

        acts = []
        for aid in ids:
            try:
                a = client.safe_call("get_activity", aid)
                acts.append(project_activity(a, unit))
            except Exception:
                acts.append({"activityId": aid, "error": "Not found"})

        return to_json({"data": {"activities": acts, "count": len(acts)}})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))

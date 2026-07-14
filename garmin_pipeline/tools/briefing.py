"""Daily briefing tool — one call that gathers everything a daily coaching
agent needs.

Replaces the morning fan-out of query_sleep_data + query_heart_rate_data +
query_health_summary + get_performance_metrics + query_activities +
query_calendar_events with a single curated snapshot, plus deterministic
readiness flags so the agent doesn't have to re-derive thresholds every day.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.tools._format import (
    error_json,
    fmt_dist,
    fmt_dur,
    is_agent_workout,
    project_activity,
    to_json,
)
from garmin_pipeline.tools.health import _curate_training_status

# Keys dropped from training-readiness entries (ids/timestamps, not signal).
_NOISE_KEYS = (
    "userProfilePK", "userProfilePk", "deviceId", "timestamp", "timestampLocal",
    "calendarDate", "sleepHistoryFactorFeedback", "recoveryTimeFactorFeedback",
)


def _trim_scalars(d: Any) -> dict:
    """Scalar entries of a dict minus id/timestamp noise."""
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items()
            if not isinstance(v, (dict, list)) and k not in _NOISE_KEYS}


def _sleep_brief(sleep: Any) -> dict | None:
    """Compact one-night sleep summary from a raw get_sleep_data payload."""
    if not isinstance(sleep, dict):
        return None
    dto = sleep.get("dailySleepDTO") or {}
    if not dto.get("sleepTimeSeconds"):
        return None
    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    out = {
        "date": dto.get("calendarDate"),
        "duration_s": dto.get("sleepTimeSeconds"),
        "deep_s": dto.get("deepSleepSeconds"),
        "light_s": dto.get("lightSleepSeconds"),
        "rem_s": dto.get("remSleepSeconds"),
        "awake_s": dto.get("awakeSleepSeconds"),
        "score": overall.get("value"),
        "score_qualifier": overall.get("qualifierKey"),
        "overnight_hrv": dto.get("avgOvernightHrv"),
        "resting_hr": sleep.get("restingHeartRate") or dto.get("restingHeartRate"),
        "body_battery_change": sleep.get("bodyBatteryChange") or dto.get("bodyBatteryChange"),
        "avg_sleep_stress": dto.get("avgSleepStress"),
    }
    return out


def _readiness_flags(
    sleep: dict | None,
    hrv_summary: dict | None,
    resting_hr: int | float | None,
    resting_hr_7d: int | float | None,
    body_battery_latest: int | float | None,
    readiness: dict | None,
) -> list[str]:
    """Deterministic red-flag detection for the daily check-in.

    Thresholds mirror run-skill's adaptation rules so the agent gets the same
    answer every day instead of re-deriving them from raw numbers.
    """
    flags: list[str] = []

    if sleep:
        dur = sleep.get("duration_s")
        if isinstance(dur, (int, float)) and dur < 6 * 3600:
            flags.append("sleep_short")
        score = sleep.get("score")
        if isinstance(score, (int, float)) and score < 60:
            flags.append("sleep_score_low")

    if hrv_summary:
        last_night = hrv_summary.get("lastNightAvg")
        baseline = hrv_summary.get("baseline") or {}
        low = baseline.get("balancedLow")
        if isinstance(last_night, (int, float)) and isinstance(low, (int, float)) \
                and last_night < low:
            flags.append("hrv_below_baseline")
        if hrv_summary.get("status") in ("LOW", "UNBALANCED", "POOR"):
            flags.append("hrv_status_" + str(hrv_summary["status"]).lower())

    if isinstance(resting_hr, (int, float)) and isinstance(resting_hr_7d, (int, float)) \
            and resting_hr >= resting_hr_7d + 5:
        flags.append("resting_hr_elevated")

    if isinstance(body_battery_latest, (int, float)) and body_battery_latest < 40:
        flags.append("body_battery_low")

    if readiness:
        level = str(readiness.get("level") or "").upper()
        if level in ("LOW", "POOR"):
            flags.append("training_readiness_low")

    return flags


def _is_run(activity: dict) -> bool:
    t = (activity.get("activityType") or {}).get("typeKey", "")
    return "running" in t


def _load_window(activities: list[dict], start: str, end: str, unit: str) -> dict:
    """Aggregate activity load for [start, end] (inclusive, YYYY-MM-DD)."""
    acts = [a for a in activities
            if start <= (a.get("startTimeLocal") or "")[:10] <= end]
    dist = sum(a.get("distance") or 0 for a in acts)
    dur = sum(a.get("duration") or 0 for a in acts)
    run_dist = sum(a.get("distance") or 0 for a in acts if _is_run(a))
    out = {
        "activities": len(acts),
        "distance": fmt_dist(dist, unit),
        "distance_m": round(dist),
        "run_distance": fmt_dist(run_dist, unit),
        "time": fmt_dur(dur),
    }
    return out


def _calendar_items(client: Any, start: datetime, months_ahead_end: datetime) -> list[dict]:
    """Scheduled workouts + races from every month touching [start, end]."""
    items: list[dict] = []
    seen: set[str] = set()
    current = datetime(start.year, start.month, 1)
    while current <= months_ahead_end:
        try:
            sw = client.safe_call("get_scheduled_workouts", current.year, current.month)
            for item in (sw or {}).get("calendarItems", []):
                d = (item.get("date") or "")[:10]
                key = f"{d}:{item.get('title', '')}"
                if not d or key in seen:
                    continue
                seen.add(key)
                items.append(item)
        except Exception:
            pass
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1)
        else:
            current = datetime(current.year, current.month + 1, 1)
    items.sort(key=lambda i: i.get("date") or "9999")
    return items


def get_daily_briefing(
    days_ahead: int = 7,
    race_horizon_days: int = 120,
    unit: str = "imperial",
) -> str:
    """Get the complete daily coaching snapshot in one call: readiness (sleep,
    HRV, resting HR, body battery, training readiness), training status,
    yesterday's and today's activities, 7/28-day training load, scheduled
    workouts for the next days_ahead days, and upcoming races.

    Includes analysis.flags — deterministic readiness red flags
    (sleep_short, hrv_below_baseline, resting_hr_elevated, ...) — so daily
    check-in agents rarely need any follow-up call.

    Args:
        days_ahead: How many days of scheduled workouts to include (default 7).
        race_horizon_days: How far ahead to look for races (default 120).
        unit: "imperial" (miles, default) or "metric" (km).
    """
    try:
        client = get_client()
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        data: dict[str, Any] = {"date": today}
        readiness_block: dict[str, Any] = {}

        # --- Sleep (last night = today's date; fall back to yesterday) ------
        sleep_brief = None
        for d in (today, yesterday):
            try:
                sleep_brief = _sleep_brief(client.safe_call("get_sleep_data", d))
            except Exception:
                sleep_brief = None
            if sleep_brief:
                break
        readiness_block["sleep"] = sleep_brief

        # --- HRV -------------------------------------------------------------
        hrv_summary = None
        try:
            hrv = client.safe_call("get_hrv_data", today)
            if isinstance(hrv, dict):
                hrv_summary = hrv.get("hrvSummary") or None
        except Exception:
            pass
        readiness_block["hrv"] = hrv_summary

        # --- Daily summary: RHR, body battery, stress -------------------------
        resting_hr = resting_hr_7d = bb_latest = None
        try:
            summary = client.safe_call("get_user_summary", today)
            if isinstance(summary, dict):
                resting_hr = summary.get("restingHeartRate")
                resting_hr_7d = summary.get("lastSevenDaysAvgRestingHeartRate")
                bb_latest = summary.get("bodyBatteryMostRecentValue")
                readiness_block["resting_hr"] = {
                    "today": resting_hr, "seven_day_avg": resting_hr_7d}
                readiness_block["body_battery"] = {
                    "latest": bb_latest,
                    "high": summary.get("bodyBatteryHighestValue"),
                    "low": summary.get("bodyBatteryLowestValue"),
                }
                readiness_block["stress"] = {
                    "avg": summary.get("averageStressLevel"),
                    "max": summary.get("maxStressLevel"),
                }
        except Exception:
            pass

        # --- Training readiness (Garmin's own score) --------------------------
        tr = None
        try:
            raw_tr = client.safe_call("get_training_readiness", today)
            entry = raw_tr[0] if isinstance(raw_tr, list) and raw_tr else raw_tr
            tr = _trim_scalars(entry) or None
        except Exception:
            pass
        readiness_block["training_readiness"] = tr

        data["readiness"] = readiness_block

        # --- Training status / load balance -----------------------------------
        try:
            ts = client.safe_call("get_training_status", today)
            data["training_status"] = _curate_training_status(ts)
        except Exception:
            data["training_status"] = None

        # --- Activities: last 28 days, one call --------------------------------
        activities: list[dict] = []
        try:
            start_28 = (now - timedelta(days=27)).strftime("%Y-%m-%d")
            activities = client.safe_call(
                "get_activities_by_date", start_28, today, "") or []
        except Exception:
            activities = []

        data["yesterday_activities"] = [
            project_activity(a, unit) for a in activities
            if (a.get("startTimeLocal") or "")[:10] == yesterday]
        data["today_activities"] = [
            project_activity(a, unit) for a in activities
            if (a.get("startTimeLocal") or "")[:10] == today]

        last7_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        prev21_start = (now - timedelta(days=27)).strftime("%Y-%m-%d")
        prev21_end = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        last7 = _load_window(activities, last7_start, today, unit)
        prev21 = _load_window(activities, prev21_start, prev21_end, unit)
        data["load"] = {
            "last_7_days": last7,
            "prev_21_days_weekly_avg": {
                "distance": fmt_dist(prev21["distance_m"] / 3, unit),
                "distance_m": round(prev21["distance_m"] / 3),
            },
        }

        # --- Calendar: scheduled workouts + races ------------------------------
        horizon_end = now + timedelta(days=race_horizon_days)
        items = _calendar_items(client, now, horizon_end)
        workouts_until = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        upcoming_workouts = []
        upcoming_races = []
        for item in items:
            d = (item.get("date") or "")[:10]
            if d < today:
                continue
            if item.get("isRace"):
                days_until = (datetime.strptime(d, "%Y-%m-%d") -
                              datetime(now.year, now.month, now.day)).days
                race = {"date": d, "name": item.get("title"), "days_until": days_until}
                if item.get("distance"):
                    race["distance_m"] = item["distance"]
                upcoming_races.append(race)
            elif d <= workouts_until:
                upcoming_workouts.append({
                    "date": d,
                    "name": item.get("title"),
                    "workout_id": item.get("workoutId"),
                    # agent_managed: carries the 🤖 tag → agents may rewrite
                    # or delete it. False → athlete-created, hands off.
                    "agent_managed": is_agent_workout(item.get("title")),
                })
        data["upcoming"] = {"workouts": upcoming_workouts, "races": upcoming_races}

        # --- Flags -------------------------------------------------------------
        flags = _readiness_flags(
            sleep_brief, hrv_summary, resting_hr, resting_hr_7d, bb_latest, tr)
        insights = []
        if not flags:
            insights.append("No readiness red flags — proceed as planned")
        if upcoming_races:
            r = upcoming_races[0]
            insights.append(f"Next race: {r['name']} in {r['days_until']} day(s)")

        return to_json({"data": data, "analysis": {"flags": flags, "insights": insights}})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))

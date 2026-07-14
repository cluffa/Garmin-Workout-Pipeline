"""Health & wellness query tools."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.time_utils import parse_date_string
from garmin_pipeline.tools._format import error_json, scalars_of, strip_empty, to_json

# Daily summary fields worth keeping by default (get_user_summary returns ~90
# fields; the rest are nulls, privacy rules, and internal ids — raw=True has all).
_SUMMARY_KEYS = (
    "totalKilocalories", "activeKilocalories", "bmrKilocalories",
    "totalSteps", "dailyStepGoal", "totalDistanceMeters",
    "highlyActiveSeconds", "activeSeconds", "sedentarySeconds", "sleepingSeconds",
    "moderateIntensityMinutes", "vigorousIntensityMinutes", "intensityMinutesGoal",
    "floorsAscended", "floorsDescended", "floorsAscendedInMeters",
    "minHeartRate", "maxHeartRate", "restingHeartRate",
    "lastSevenDaysAvgRestingHeartRate",
    "averageStressLevel", "maxStressLevel", "stressDuration",
    "restStressDuration", "activityStressDuration", "lowStressDuration",
    "mediumStressDuration", "highStressDuration", "stressQualifier",
    "bodyBatteryChargedValue", "bodyBatteryDrainedValue",
    "bodyBatteryHighestValue", "bodyBatteryLowestValue",
    "bodyBatteryMostRecentValue",
    "averageSpo2", "lowestSpo2", "latestSpo2",
    "avgWakingRespirationValue", "highestRespirationValue",
    "lowestRespirationValue", "latestRespirationValue",
)


def _curate_summary(summary: Any) -> Any:
    if not isinstance(summary, dict):
        return summary
    out = {k: summary[k] for k in _SUMMARY_KEYS if summary.get(k) is not None}
    for k in ("floorsAscended", "floorsDescended", "floorsAscendedInMeters"):
        if isinstance(out.get(k), float):
            out[k] = round(out[k], 1)
    return out


def _series_min_max(day: dict) -> dict:
    """Extract min/max level from a bodyBatteryValuesArray using its descriptors."""
    try:
        descriptors = day.get("bodyBatteryValueDescriptorDTOList") or []
        idx = next(d["bodyBatteryValueDescriptorIndex"] for d in descriptors
                   if d.get("bodyBatteryValueDescriptorKey") == "bodyBatteryLevel")
        levels = [e[idx] for e in day.get("bodyBatteryValuesArray") or []
                  if isinstance(e, list) and len(e) > idx and isinstance(e[idx], (int, float))]
        if levels:
            return {"highest": max(levels), "lowest": min(levels), "latest": levels[-1]}
    except Exception:
        pass
    return {}


def _curate_body_battery(bb: Any) -> Any:
    """Keep per-day scalars + computed high/low; drop per-reading arrays."""
    if not isinstance(bb, list):
        return scalars_of(bb) if isinstance(bb, dict) else bb
    return [{**scalars_of(day), **_series_min_max(day)} if isinstance(day, dict) else day
            for day in bb]


def _curate_training_status(ts: Any) -> Any:
    """Extract the latest status/VO2max/load-balance from the nested device maps."""
    if not isinstance(ts, dict):
        return ts
    try:
        out: dict[str, Any] = {}
        vo2 = ts.get("mostRecentVO2Max") or {}
        if isinstance(vo2.get("generic"), dict):
            out["vo2max"] = scalars_of(vo2["generic"])
        if isinstance(vo2.get("cycling"), dict):
            out["vo2max_cycling"] = scalars_of(vo2["cycling"])

        tlb = ts.get("mostRecentTrainingLoadBalance") or {}
        tlb_map = tlb.get("metricsTrainingLoadBalanceDTOMap") or {}
        for entry in tlb_map.values():
            if isinstance(entry, dict):
                out["training_load_balance"] = scalars_of(entry)
                break

        mts = ts.get("mostRecentTrainingStatus") or {}
        status_map = mts.get("latestTrainingStatusData") or {}
        for entry in status_map.values():
            if isinstance(entry, dict):
                status = scalars_of(entry)
                acute = entry.get("acuteTrainingLoadDTO")
                if isinstance(acute, dict):
                    status["acuteTrainingLoad"] = scalars_of(acute)
                out["training_status"] = status
                break
        return out if out else strip_empty(ts)
    except Exception:
        return strip_empty(ts)


def query_health_summary(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cursor: str | None = None,
    limit: str | int | None = None,
    include_body_battery: bool = True,
    include_training_readiness: bool = True,
    include_training_status: bool = True,
    unit: str = "imperial",
    raw: bool = False,
) -> str:
    """Get daily health snapshot: activity/stress/HR/body-battery summary, plus
    training readiness and training status.

    Single date (date=...) or paginated range (start_date + end_date). Returns
    curated summaries; set raw=true for complete unabridged Garmin payloads
    (very large).
    """
    try:
        client = get_client()

        if limit is not None:
            limit = int(limit) if isinstance(limit, str) else limit
            limit = max(1, min(int(limit), 30))
        else:
            limit = 7

        def day_summary(d: str) -> Any:
            try:
                summary = client.safe_call("get_user_summary", d)
                return summary if raw else _curate_summary(summary)
            except Exception:
                return None

        # Single date mode
        if date and not (start_date and end_date):
            dt = parse_date_string(date)
            d = dt.strftime("%Y-%m-%d")
            result: dict[str, Any] = {"date": d, "summary": day_summary(d)}

            if include_body_battery:
                try:
                    bb = client.safe_call("get_body_battery", d)
                    result["body_battery"] = bb if raw else _curate_body_battery(bb)
                except Exception:
                    result["body_battery"] = None

            if include_training_readiness:
                try:
                    result["training_readiness"] = client.safe_call(
                        "get_training_readiness", d)
                except Exception:
                    result["training_readiness"] = None

            if include_training_status:
                try:
                    ts = client.safe_call("get_training_status", d)
                    result["training_status"] = ts if raw else _curate_training_status(ts)
                except Exception:
                    result["training_status"] = None

            return to_json({"data": result})

        # Range mode
        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days

            current_page = 1
            if cursor:
                try:
                    import base64
                    import json
                    decoded = json.loads(base64.b64decode(cursor).decode())
                    current_page = decoded.get("page", 1)
                except Exception:
                    pass

            all_dates = [(s + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]
            offset = (current_page - 1) * limit
            page_dates = all_dates[offset:offset + limit + 1]
            has_more = len(page_dates) > limit
            page_dates = page_dates[:limit]

            summaries = [{"date": d, "summary": day_summary(d)} for d in page_dates]

            next_cursor = None
            if has_more:
                import base64
                import json
                next_cursor = base64.b64encode(
                    json.dumps({"page": current_page + 1}).encode()
                ).decode()

            return to_json({
                "data": {"summaries": summaries, "count": len(summaries)},
                "pagination": {"cursor": next_cursor, "has_more": has_more},
            })

        # Default: today
        d = datetime.now().strftime("%Y-%m-%d")
        return to_json({"data": {"date": d, "summary": day_summary(d)}})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


# Redundant nested blocks inside dailySleepDTO (derivable from duration/score;
# kept with raw=True).
_SLEEP_DTO_DROP = ("sleepNeed", "nextSleepNeed", "sleepAlignment", "wellnessEpochList")


def _curate_sleep(sleep: Any) -> Any:
    """Keep the nightly summary (scores, stages, HRV/SpO2/RHR averages); drop
    per-minute movement/level/HR/stress arrays and redundant need/alignment
    blocks."""
    if not isinstance(sleep, dict):
        return sleep
    out: dict[str, Any] = scalars_of(sleep)
    dto = sleep.get("dailySleepDTO")
    if isinstance(dto, dict):
        dto = {k: v for k, v in dto.items() if k not in _SLEEP_DTO_DROP}
        scores = dto.get("sleepScores")
        if isinstance(scores, dict):
            # Each score entry carries optimal-range metadata; keep the verdict.
            dto["sleepScores"] = {
                name: {k: v for k, v in entry.items()
                       if k in ("value", "qualifierKey")}
                if isinstance(entry, dict) else entry
                for name, entry in scores.items()
            }
        out["dailySleepDTO"] = strip_empty(dto)
    return out


def query_sleep_data(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    raw: bool = False,
) -> str:
    """Get sleep data: duration, stages (deep/light/REM/awake), sleep scores,
    overnight HRV, resting HR, SpO2 and respiration averages, body battery change.

    Single date, date range, or yesterday (default). Per-minute time series
    (movement, sleep levels, HR, stress) are omitted unless raw=true (very large).
    """
    try:
        client = get_client()

        def fetch(d: str) -> Any:
            sleep = client.safe_call("get_sleep_data", d)
            return sleep if raw else _curate_sleep(sleep)

        if date:
            d = parse_date_string(date).strftime("%Y-%m-%d")
            return to_json({"data": {"sleep": fetch(d), "date": d}})

        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days
            results = []
            for i in range(days + 1):
                d = (s + timedelta(days=i)).strftime("%Y-%m-%d")
                try:
                    results.append({"date": d, "sleep": fetch(d)})
                except Exception:
                    results.append({"date": d, "sleep": None})
            return to_json({"data": {"sleep_data": results, "count": len(results)}})

        d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return to_json({"data": {"sleep": fetch(d), "date": d}})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


def _curate_heart_rate(hr: Any) -> Any:
    """Keep daily HR summary; replace the ~720-point series with computed stats."""
    if not isinstance(hr, dict):
        return hr
    out = scalars_of(hr)
    values = hr.get("heartRateValues") or []
    samples = [v[1] for v in values
               if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float))]
    if samples:
        out["averageHeartRate"] = round(sum(samples) / len(samples))
        out["samples"] = len(samples)
    return out


def query_heart_rate_data(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_resting: bool = True,
    raw: bool = False,
) -> str:
    """Get daily heart rate data: resting, min, max, 7-day average resting HR,
    and computed daily average.

    Single date, date range, or today (default). The intraday 2-minute sample
    series is omitted unless raw=true (very large).
    """
    try:
        client = get_client()

        def fetch(d: str) -> Any:
            hr = client.safe_call("get_heart_rates", d)
            return hr if raw else _curate_heart_rate(hr)

        if date:
            d = parse_date_string(date).strftime("%Y-%m-%d")
            return to_json({"data": {"heart_rate": fetch(d), "date": d}})

        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days
            results = []
            for i in range(days + 1):
                d = (s + timedelta(days=i)).strftime("%Y-%m-%d")
                try:
                    results.append({"date": d, "heart_rate": fetch(d)})
                except Exception:
                    results.append({"date": d, "heart_rate": None})
            return to_json({"data": {"heart_rate_data": results, "count": len(results)}})

        d = datetime.now().strftime("%Y-%m-%d")
        return to_json({"data": {"heart_rate": fetch(d), "date": d}})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


def _curate_steps(steps: Any) -> Any:
    """Sum 15-minute step chunks into a daily total + activity-level minutes."""
    if not isinstance(steps, list):
        return steps
    total = 0
    levels: dict[str, int] = {}
    for chunk in steps:
        if not isinstance(chunk, dict):
            continue
        total += chunk.get("steps") or 0
        level = chunk.get("primaryActivityLevel")
        if level:
            levels[level] = levels.get(level, 0) + 15
    return {"total_steps": total, "minutes_by_activity_level": levels}


def _curate_floors(floors: Any) -> Any:
    """Sum ascended/descended from the intraday floors array."""
    if not isinstance(floors, dict):
        return floors
    out = scalars_of(floors)
    try:
        descriptors = floors.get("floorsValueDescriptorDTOList") or []
        keys = {d["key"]: d["index"] for d in descriptors
                if isinstance(d, dict) and "key" in d and "index" in d}
        values = floors.get("floorValuesArray") or []
        for name, out_key in (("floorsAscended", "floorsAscended"),
                              ("floorsDescended", "floorsDescended")):
            idx = keys.get(name)
            if idx is not None:
                out[out_key] = round(sum(
                    v[idx] for v in values
                    if isinstance(v, list) and len(v) > idx
                    and isinstance(v[idx], (int, float))), 1)
    except Exception:
        pass
    return out


def query_activity_metrics(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    metrics: str = "steps,stress",
    unit: str = "imperial",
    raw: bool = False,
) -> str:
    """Get daily activity metrics. Available: steps, stress, respiration, spo2,
    floors (comma-separated; default "steps,stress").

    Returns daily totals/averages; intraday sample arrays are omitted unless
    raw=true (very large). Single date, date range, or today (default).
    """
    try:
        client = get_client()
        metric_list = [m.strip() for m in metrics.split(",")]

        def fetch(m: str, d: str) -> Any:
            if m == "steps":
                data = client.safe_call("get_steps_data", d)
                return data if raw else _curate_steps(data)
            if m == "stress":
                data = client.safe_call("get_stress_data", d)
                return data if raw else scalars_of(data)
            if m == "respiration":
                data = client.safe_call("get_respiration_data", d)
                return data if raw else scalars_of(data)
            if m == "spo2":
                data = client.safe_call("get_spo2_data", d)
                return data if raw else scalars_of(data)
            if m == "floors":
                data = client.safe_call("get_floors", d)
                return data if raw else _curate_floors(data)
            return None

        def day_metrics(d: str) -> dict:
            entry: dict[str, Any] = {"date": d}
            for m in metric_list:
                try:
                    entry[m] = fetch(m, d)
                except Exception:
                    entry[m] = None
            return entry

        if date:
            d = parse_date_string(date).strftime("%Y-%m-%d")
            return to_json({"data": day_metrics(d)})

        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days
            results = [day_metrics((s + timedelta(days=i)).strftime("%Y-%m-%d"))
                       for i in range(days + 1)]
            return to_json({"data": {"metrics": results, "count": len(results)}})

        d = datetime.now().strftime("%Y-%m-%d")
        return to_json({"data": day_metrics(d)})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))

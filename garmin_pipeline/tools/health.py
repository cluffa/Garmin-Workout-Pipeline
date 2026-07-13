"""Health & wellness query tools."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.time_utils import parse_date_string


def query_health_summary(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cursor: str | None = None,
    limit: str | int | None = None,
    include_body_battery: bool = True,
    include_training_readiness: bool = True,
    include_training_status: bool = True,
    unit: str = "metric",
) -> str:
    """Get comprehensive daily health snapshot with pagination support.

    Includes stats, user summary, training readiness, training status,
    Body Battery, and Body Battery events.

    Supports single date or date range queries with pagination.
    """
    try:
        client = get_client()

        if limit is not None:
            limit = int(limit) if isinstance(limit, str) else limit
            limit = max(1, min(int(limit), 30))
        else:
            limit = 7

        # Single date mode
        if date and not (start_date and end_date):
            dt = parse_date_string(date)
            d = dt.strftime("%Y-%m-%d")
            result: dict[str, Any] = {"date": d}

            try:
                result["stats"] = client.safe_call("get_stats", d)
            except Exception:
                result["stats"] = None

            try:
                result["user_summary"] = client.safe_call("get_user_summary", d)
            except Exception:
                result["user_summary"] = None

            if include_body_battery:
                try:
                    result["body_battery"] = client.safe_call("get_body_battery", d)
                except Exception:
                    result["body_battery"] = None

            if include_training_readiness:
                try:
                    result["training_readiness"] = client.safe_call("get_training_readiness", d)
                except Exception:
                    result["training_readiness"] = None

            if include_training_status:
                try:
                    result["training_status"] = client.safe_call("get_training_status", d)
                except Exception:
                    result["training_status"] = None

            return json.dumps({
                "data": result,
                "metadata": {"date": d, "unit": unit, "fetched_at": datetime.now().isoformat()}
            })

        # Range mode
        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days

            # Parse cursor
            current_page = 1
            if cursor:
                try:
                    import base64
                    decoded = json.loads(base64.b64decode(cursor).decode())
                    current_page = decoded.get("page", 1)
                except Exception:
                    pass

            # Generate date range
            all_dates = [(s + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]
            offset = (current_page - 1) * limit
            page_dates = all_dates[offset:offset + limit + 1]
            has_more = len(page_dates) > limit
            page_dates = page_dates[:limit]

            summaries = []
            for d in page_dates:
                summary: dict[str, Any] = {"date": d}
                try:
                    summary["stats"] = client.safe_call("get_stats", d)
                except Exception:
                    pass
                try:
                    summary["user_summary"] = client.safe_call("get_user_summary", d)
                except Exception:
                    pass
                summaries.append(summary)

            next_cursor = None
            if has_more:
                import base64
                next_cursor = base64.b64encode(
                    json.dumps({"page": current_page + 1}).encode()
                ).decode()

            return json.dumps({
                "data": {"summaries": summaries, "count": len(summaries)},
                "pagination": {"cursor": next_cursor, "has_more": has_more,
                    "limit": limit, "returned": len(summaries)},
                "metadata": {"start_date": start_date, "end_date": end_date,
                    "unit": unit, "fetched_at": datetime.now().isoformat()}
            })

        # Default: today
        d = datetime.now().strftime("%Y-%m-%d")
        result = {"date": d}
        try:
            result["stats"] = client.safe_call("get_stats", d)
        except Exception:
            result["stats"] = None
        return json.dumps({
            "data": result,
            "metadata": {"date": d, "unit": unit, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})


def query_sleep_data(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get sleep data and analysis.

    Retrieves sleep duration, sleep stages (deep, light, REM), sleep scores,
    HRV, resting heart rate, and body battery impact.
    """
    try:
        client = get_client()

        if date:
            dt = parse_date_string(date)
            d = dt.strftime("%Y-%m-%d")
            sleep = client.safe_call("get_sleep_data", d)
            return json.dumps({
                "data": {"sleep": sleep, "date": d},
                "metadata": {"date": d, "fetched_at": datetime.now().isoformat()}
            })

        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days
            results = []
            for i in range(days + 1):
                d = (s + timedelta(days=i)).strftime("%Y-%m-%d")
                try:
                    sleep = client.safe_call("get_sleep_data", d)
                    results.append({"date": d, "sleep": sleep})
                except Exception:
                    results.append({"date": d, "sleep": None})
            return json.dumps({
                "data": {"sleep_data": results, "count": len(results)},
                "metadata": {"start_date": start_date, "end_date": end_date,
                    "fetched_at": datetime.now().isoformat()}
            })

        # Default: yesterday
        d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        sleep = client.safe_call("get_sleep_data", d)
        return json.dumps({
            "data": {"sleep": sleep, "date": d},
            "metadata": {"date": d, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})


def query_heart_rate_data(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_resting: bool = True,
) -> str:
    """Get heart rate data.

    Retrieves heart rate data including resting HR, average HR, min/max values.
    """
    try:
        client = get_client()

        if date:
            dt = parse_date_string(date)
            d = dt.strftime("%Y-%m-%d")
            hr_data = client.safe_call("get_heart_rates", d)
            return json.dumps({
                "data": {"heart_rate": hr_data, "date": d},
                "metadata": {"date": d, "fetched_at": datetime.now().isoformat()}
            })

        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days
            results = []
            for i in range(days + 1):
                d = (s + timedelta(days=i)).strftime("%Y-%m-%d")
                try:
                    hr_data = client.safe_call("get_heart_rates", d)
                    results.append({"date": d, "heart_rate": hr_data})
                except Exception:
                    results.append({"date": d, "heart_rate": None})
            return json.dumps({
                "data": {"heart_rate_data": results, "count": len(results)},
                "metadata": {"start_date": start_date, "end_date": end_date,
                    "fetched_at": datetime.now().isoformat()}
            })

        d = datetime.now().strftime("%Y-%m-%d")
        hr_data = client.safe_call("get_heart_rates", d)
        return json.dumps({
            "data": {"heart_rate": hr_data, "date": d},
            "metadata": {"date": d, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})


def query_activity_metrics(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    metrics: str = "steps,stress",
    unit: str = "metric",
) -> str:
    """Get activity metrics (steps, stress, etc.).

    Includes steps, stress, respiration, SpO2, floors climbed, hydration,
    blood pressure, and body composition.

    Select specific metrics to retrieve using the metrics parameter.
    Default: steps and stress.
    """
    try:
        client = get_client()
        metric_list = [m.strip() for m in metrics.split(",")]

        if date:
            dt = parse_date_string(date)
            d = dt.strftime("%Y-%m-%d")
            result = {"date": d}
            for m in metric_list:
                try:
                    if m == "steps":
                        result["steps"] = client.safe_call("get_steps_data", d)
                    elif m == "stress":
                        result["stress"] = client.safe_call("get_stress_data", d)
                    elif m == "respiration":
                        result["respiration"] = client.safe_call("get_respiration_data", d)
                    elif m == "spo2":
                        result["spo2"] = client.safe_call("get_spo2_data", d)
                    elif m == "floors":
                        result["floors"] = client.safe_call("get_floors", d)
                except Exception:
                    result[m] = None
            return json.dumps({
                "data": result,
                "metadata": {"date": d, "unit": unit, "fetched_at": datetime.now().isoformat()}
            })

        if start_date and end_date:
            s = parse_date_string(start_date)
            e = parse_date_string(end_date)
            days = (e - s).days
            results = []
            for i in range(days + 1):
                d = (s + timedelta(days=i)).strftime("%Y-%m-%d")
                entry = {"date": d}
                for m in metric_list:
                    try:
                        if m == "steps":
                            entry["steps"] = client.safe_call("get_steps_data", d)
                        elif m == "stress":
                            entry["stress"] = client.safe_call("get_stress_data", d)
                    except Exception:
                        entry[m] = None
                results.append(entry)
            return json.dumps({
                "data": {"metrics": results, "count": len(results)},
                "metadata": {"start_date": start_date, "end_date": end_date,
                    "unit": unit, "fetched_at": datetime.now().isoformat()}
            })

        d = datetime.now().strftime("%Y-%m-%d")
        result = {"date": d}
        for m in metric_list:
            try:
                if m == "steps":
                    result["steps"] = client.safe_call("get_steps_data", d)
            except Exception:
                result[m] = None
        return json.dumps({
            "data": result,
            "metadata": {"date": d, "unit": unit, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})

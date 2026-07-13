"""User profile and goals tools."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client


def get_user_profile(
    include_stats: bool = True,
    include_prs: bool = True,
    include_devices: bool = True,
) -> str:
    """Get comprehensive user profile with optional stats, personal records, and devices."""
    try:
        client = get_client()
        data: dict[str, Any] = {}

        full_name = client.safe_call("get_full_name")
        data["profile"] = {"full_name": full_name}

        if include_stats:
            today = datetime.now().strftime("%Y-%m-%d")
            try:
                data["stats"] = client.safe_call("get_stats", today)
            except Exception:
                data["stats"] = None
            try:
                data["user_summary"] = client.safe_call("get_user_summary", today)
            except Exception:
                data["user_summary"] = None

        if include_prs:
            try:
                data["personal_records"] = client.safe_call("get_personal_record")
            except Exception:
                data["personal_records"] = None

        if include_devices:
            try:
                data["devices"] = client.safe_call("get_devices")
            except Exception:
                data["devices"] = None
            try:
                data["primary_device"] = client.safe_call("get_primary_training_device")
            except Exception:
                data["primary_device"] = None

        insights = []
        if full_name:
            insights.append(f"Profile for: {full_name}")
        if include_devices and isinstance(data.get("devices"), list):
            insights.append(f"{len(data['devices'])} device(s) registered")

        return json.dumps({
            "data": data,
            "analysis": {"insights": insights} if insights else None,
            "metadata": {"include_stats": include_stats, "include_prs": include_prs,
                "include_devices": include_devices, "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})


def query_goals_and_records(
    include_goals: bool = True,
    include_prs: bool = True,
    include_race_predictions: bool = True,
) -> str:
    """Get goals, personal records, and race predictions.

    Returns your activity goals, personal best performances,
    and predicted race times based on recent training.
    """
    try:
        client = get_client()
        data: dict[str, Any] = {}

        if include_goals:
            try:
                data["goals"] = client.safe_call("get_goals")
            except Exception:
                data["goals"] = None

        if include_prs:
            try:
                data["personal_records"] = client.safe_call("get_personal_record")
            except Exception:
                data["personal_records"] = None

        if include_race_predictions:
            try:
                data["race_predictions"] = client.safe_call("get_race_predictions")
            except Exception:
                data["race_predictions"] = None

        insights = [f"Available data: {', '.join(k for k, v in data.items() if v is not None)}"]

        return json.dumps({
            "data": data,
            "analysis": {"insights": insights},
            "metadata": {"includes": {"goals": include_goals, "prs": include_prs,
                "race_predictions": include_race_predictions},
                "fetched_at": datetime.now().isoformat()}
        })

    except GarminAPIError as e:
        return json.dumps({"error": {"type": "api_error", "message": e.message}})
    except Exception as e:
        return json.dumps({"error": {"type": "internal_error", "message": str(e)}})

"""User profile and goals tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from garmin_pipeline.client import GarminAPIError, get_client
from garmin_pipeline.tools._format import error_json, to_json

# Device fields worth keeping by default (full device dicts include hundreds of
# capability flags — raw=True has everything).
_DEVICE_KEYS = (
    "deviceId", "productDisplayName", "displayName", "deviceTypePk",
    "softwareVersion", "firmwareVersion", "lastSyncTime", "batteryLevel",
    "batteryStatus", "primaryActivityTracker", "deviceStatus",
)


def _curate_device(dev: Any) -> Any:
    if not isinstance(dev, dict):
        return dev
    return {k: dev[k] for k in _DEVICE_KEYS if dev.get(k) is not None}


def get_user_profile(
    include_stats: bool = True,
    include_prs: bool = True,
    include_devices: bool = True,
    raw: bool = False,
) -> str:
    """Get user profile: name, today's activity summary, personal records,
    and registered devices.

    Returns curated summaries; set raw=true for complete unabridged Garmin
    payloads (large).
    """
    try:
        client = get_client()
        data: dict[str, Any] = {}

        full_name = client.safe_call("get_full_name")
        data["profile"] = {"full_name": full_name}

        if include_stats:
            today = datetime.now().strftime("%Y-%m-%d")
            try:
                summary = client.safe_call("get_user_summary", today)
                if not raw:
                    from garmin_pipeline.tools.health import _curate_summary
                    summary = _curate_summary(summary)
                data["today_summary"] = summary
            except Exception:
                data["today_summary"] = None

        if include_prs:
            try:
                data["personal_records"] = client.safe_call("get_personal_record")
            except Exception:
                data["personal_records"] = None

        if include_devices:
            try:
                devices = client.safe_call("get_devices")
                if not raw and isinstance(devices, list):
                    devices = [_curate_device(d) for d in devices]
                data["devices"] = devices
            except Exception:
                data["devices"] = None
            try:
                primary = client.safe_call("get_primary_training_device")
                if not raw and isinstance(primary, dict):
                    curated = {k: v for k, v in primary.items()
                               if k in ("primaryTrainingDevice", "trainingStatusPausedDate")}
                    if curated:
                        primary = curated
                data["primary_device"] = primary
            except Exception:
                data["primary_device"] = None

        return to_json({"data": data})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))


def query_goals_and_records(
    include_goals: bool = True,
    include_prs: bool = True,
    include_race_predictions: bool = True,
) -> str:
    """Get activity goals, personal records, and predicted race times based on
    recent training."""
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

        return to_json({"data": data})

    except GarminAPIError as e:
        return error_json("api_error", e.message)
    except Exception as e:
        return error_json("internal_error", str(e))

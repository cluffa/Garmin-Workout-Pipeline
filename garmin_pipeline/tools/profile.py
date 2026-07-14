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


# Garmin personal-record type ids. 1-6 are times in seconds, 7 is meters,
# 8-10 are step counts. Other ids (cycling/strength/steps-streak) pass
# through unlabeled rather than risk a wrong label.
_PR_LABELS = {
    1: "1K", 2: "1 mile", 3: "5K", 4: "10K", 5: "Half Marathon", 6: "Marathon",
    7: "Longest Run", 8: "Most Steps (day)", 9: "Most Steps (week)",
    10: "Most Steps (month)",
}
_PR_TIME_IDS = frozenset((1, 2, 3, 4, 5, 6))
_PR_DIST_IDS = frozenset((7,))


def _fmt_hms(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _curate_prs(prs: Any) -> Any:
    """Compact personal records: label, value (formatted for time/distance
    records), source activity, and date."""
    if not isinstance(prs, list):
        return prs
    out = []
    for pr in prs:
        if not isinstance(pr, dict):
            out.append(pr)
            continue
        type_id = pr.get("typeId")
        entry: dict[str, Any] = {
            "typeId": type_id,
            "label": _PR_LABELS.get(type_id),
            "value": pr.get("value"),
            "activity": pr.get("activityName"),
            "date": (pr.get("actStartDateTimeInGMTFormatted") or "")[:10] or None,
        }
        value = pr.get("value")
        if isinstance(value, (int, float)):
            entry["value"] = round(value, 2)
            if type_id in _PR_TIME_IDS:
                entry["time"] = _fmt_hms(value)
            elif type_id in _PR_DIST_IDS:
                entry["km"] = round(value / 1000, 2)
        out.append(entry)
    return out


def _curate_primary_device(primary: dict) -> dict:
    """Trim get_primary_training_device: drop the embedded RegisteredDevices
    list (~20KB of capability flags, duplicated in the devices section) and
    strip image URLs from the device-weight entries."""
    out: dict[str, Any] = {}
    for k, v in primary.items():
        if k == "RegisteredDevices":
            continue
        if isinstance(v, dict) and isinstance(v.get("deviceWeights"), list):
            v = {**v, "deviceWeights": [
                {dk: dv for dk, dv in w.items() if dk != "imageUrl"}
                if isinstance(w, dict) else w
                for w in v["deviceWeights"]]}
        out[k] = v
    return out


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
                prs = client.safe_call("get_personal_record")
                data["personal_records"] = prs if raw else _curate_prs(prs)
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
                    primary = _curate_primary_device(primary)
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
    raw: bool = False,
) -> str:
    """Get activity goals, personal records, and predicted race times based on
    recent training.

    Personal records are curated (label, value, formatted time, source
    activity, date); set raw=true for the unabridged Garmin payloads.
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
                prs = client.safe_call("get_personal_record")
                data["personal_records"] = prs if raw else _curate_prs(prs)
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

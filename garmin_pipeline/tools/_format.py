"""Shared response formatting for MCP data tools.

Tool responses are optimized for LLM context windows: compact JSON with
null/empty values stripped, and curated projections of Garmin payloads by
default. Tools that curate accept ``raw=True`` to return the complete,
unabridged Garmin payload, so no data is ever unreachable.
"""

from __future__ import annotations

import json
from typing import Any


# Workouts created by AI agents carry this prefix in their name. Workouts
# without it are athlete-created: never overwritten or deleted by agents,
# only taken into consideration.
AGENT_TAG = "\U0001f916"  # 🤖


def is_agent_workout(name: Any) -> bool:
    """True if a workout name carries the agent-ownership tag."""
    return isinstance(name, str) and name.lstrip().startswith(AGENT_TAG)


def tag_workout_name(name: str) -> str:
    """Ensure a workout name carries the agent-ownership tag."""
    return name if is_agent_workout(name) else f"{AGENT_TAG} {name}"


def strip_empty(obj: Any) -> Any:
    """Recursively drop dict entries whose value is None or empty ("", [], {}).

    Zeros and False are kept. List elements are cleaned recursively but never
    removed, so counts and ordering are preserved.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            cleaned = strip_empty(v)
            if cleaned is None or cleaned == "" or cleaned == [] or cleaned == {}:
                continue
            out[k] = cleaned
        return out
    if isinstance(obj, list):
        return [strip_empty(v) for v in obj]
    return obj


def to_json(payload: Any) -> str:
    """Serialize a response payload compactly with empty values stripped."""
    return json.dumps(strip_empty(payload), separators=(",", ":"), default=str)


def error_json(kind: str, message: str) -> str:
    """Serialize an error response."""
    return json.dumps({"error": {"type": kind, "message": message}}, separators=(",", ":"))


def scalars_of(d: Any) -> dict:
    """Return only the scalar (non-dict, non-list) entries of a dict.

    Used to keep an endpoint's summary numbers while dropping its embedded
    time-series arrays and descriptor lists.
    """
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if not isinstance(v, (dict, list))}


# ---------------------------------------------------------------------------
# Unit formatting
# ---------------------------------------------------------------------------


def fmt_dist(meters: float, unit: str) -> str:
    if unit == "imperial":
        return f"{meters / 1609.34:.2f} mi"
    return f"{meters / 1000:.2f} km"


def fmt_dur(seconds: float) -> str:
    h, m = divmod(int(seconds), 3600)
    m, sec = divmod(m, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def fmt_pace(mps: float, unit: str) -> str:
    if not mps:
        return "N/A"
    if unit == "imperial":
        spm = 1609.34 / mps
        return f"{int(spm // 60)}:{int(spm % 60):02d}/mi"
    spk = 1000 / mps
    return f"{int(spk // 60)}:{int(spk % 60):02d}/km"


def fmt_speed(mps: float, unit: str) -> str:
    if unit == "imperial":
        return f"{mps * 2.23694:.2f} mph"
    return f"{mps * 3.6:.2f} km/h"


def fmt_elev(meters: float, unit: str) -> str:
    if unit == "imperial":
        return f"{meters * 3.28084:.0f} ft"
    return f"{meters:.0f} m"


# ---------------------------------------------------------------------------
# Activity projection
# ---------------------------------------------------------------------------


def _first(src: dict, *keys: str) -> Any:
    for k in keys:
        v = src.get(k)
        if v is not None:
            return v
    return None


def project_activity(act: dict, unit: str = "imperial") -> dict:
    """Project an activity onto a compact set of training-relevant fields.

    Handles both shapes returned by Garmin: the flat list shape from
    ``get_activities_by_date`` and the nested ``summaryDTO`` shape from
    ``get_activity``. Full payloads remain available via ``raw=True``.
    """
    src = act.get("summaryDTO") if isinstance(act.get("summaryDTO"), dict) else act
    act_type = act.get("activityType") or act.get("activityTypeDTO") or {}

    out: dict[str, Any] = {
        "id": act.get("activityId"),
        "name": act.get("activityName"),
        "type": act_type.get("typeKey") if isinstance(act_type, dict) else None,
        "start": _first(src, "startTimeLocal") or act.get("startTimeLocal"),
        "location": act.get("locationName"),
    }

    dist = _first(src, "distance")
    if dist:
        out["distance_m"] = round(dist)
        out["distance"] = fmt_dist(dist, unit)
    dur = _first(src, "duration")
    if dur:
        out["duration_s"] = round(dur)
        out["duration"] = fmt_dur(dur)
    moving = _first(src, "movingDuration")
    if moving and dur and round(moving) != round(dur):
        out["moving_s"] = round(moving)
    elev = _first(src, "elevationGain")
    if elev:
        out["elev_gain"] = fmt_elev(elev, unit)
    speed = _first(src, "averageSpeed")
    if speed:
        out["pace"] = fmt_pace(speed, unit)
        out["speed"] = fmt_speed(speed, unit)

    _num = {
        "avg_hr": ("averageHR", "avgHr"),
        "max_hr": ("maxHR", "maxHr"),
        "avg_power_w": ("avgPower", "averagePower"),
        "max_power_w": ("maxPower",),
        "norm_power_w": ("normPower", "normalizedPower"),
        "avg_cadence": (
            "averageRunningCadenceInStepsPerMinute",
            "averageRunCadence",
            "averageBikingCadenceInRevPerMinute",
            "averageBikeCadence",
        ),
        "calories": ("calories",),
        "steps": ("steps",),
    }
    for key, aliases in _num.items():
        v = _first(src, *aliases)
        if v is not None:
            out[key] = round(v)

    _raw = {
        "aerobic_te": ("aerobicTrainingEffect", "trainingEffect"),
        "anaerobic_te": ("anaerobicTrainingEffect",),
        "te_label": ("trainingEffectLabel",),
        "training_load": ("activityTrainingLoad",),
        "vo2max": ("vO2MaxValue",),
    }
    for key, aliases in _raw.items():
        v = _first(src, *aliases)
        if v is not None:
            out[key] = round(v, 1) if isinstance(v, float) else v

    if act.get("personalRecord") or src.get("personalRecord"):
        out["is_pr"] = True

    return out

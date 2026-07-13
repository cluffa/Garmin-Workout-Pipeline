"""MCP server for building and uploading Garmin Connect workouts."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from garmin_pipeline.compiler import compile_workout
from garmin_pipeline.exercises import EXERCISE_REGISTRY
from garmin_pipeline.loader import load_workout
from garmin_pipeline.models import (
    BikeStep,
    CircuitGroup,
    CooldownStep,
    ExerciseStep,
    RecoveryStep,
    RestStep,
    RunStep,
    SportType,
    WarmupStep,
    Workout,
)
from garmin_pipeline.sync import GarminSync
from garmin_pipeline.tools._format import strip_empty as _strip_empty
from garmin_pipeline.tools.activities import get_activity_details, query_activities
from garmin_pipeline.tools.calendar import query_calendar_events
from garmin_pipeline.tools.health import (
    query_activity_metrics,
    query_health_summary,
    query_heart_rate_data,
    query_sleep_data,
)
from garmin_pipeline.tools.profile import get_user_profile, query_goals_and_records
from garmin_pipeline.tools.training import (
    analyze_training_period,
    compare_activities,
    get_performance_metrics,
    get_training_effect,
)
from garmin_pipeline.zones import ZoneConfig, load_zones

load_dotenv()

# Logging must go to stderr for stdio transport
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP("garmin-workout-pipeline")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

_workout: Workout | None = None
_zone_config: ZoneConfig | None = None
_circuit_stack: list[list] = []  # stack of step lists for nested circuit building

ZONES_PATH = Path.cwd() / "workouts" / "zones.yaml"


def _get_zone_config() -> ZoneConfig | None:
    global _zone_config
    if _zone_config is None and ZONES_PATH.exists():
        _zone_config = load_zones(ZONES_PATH)
    return _zone_config


def _require_workout() -> Workout:
    if _workout is None:
        raise ValueError("No workout in progress. Use create_workout first.")
    return _workout


def _active_step_list() -> list:
    """Return the step list that new steps should be added to.

    If a circuit is being built, returns the circuit's step list.
    Otherwise returns the workout's top-level step list.
    """
    if _circuit_stack:
        return _circuit_stack[-1]
    return _require_workout().steps


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------


def _format_step(step: Any, indent: int = 0) -> str:
    """Format a single step as a readable string."""
    prefix = "  " * indent
    if isinstance(step, CircuitGroup):
        lines = [f"{prefix}Circuit x{step.iterations}:"]
        for s in step.steps:
            lines.append(_format_step(s, indent + 1))
        return "\n".join(lines)

    parts = [f"{prefix}{step.step_kind.upper()}"]

    if hasattr(step, "exercise") and step.exercise:
        parts.append(f"exercise={step.exercise}")
    if hasattr(step, "distance") and step.distance:
        parts.append(f"distance={step.distance}")
    if hasattr(step, "duration") and step.duration and step.duration != "lap":
        parts.append(f"duration={step.duration}")
    elif hasattr(step, "duration") and step.duration == "lap":
        parts.append("lap button")
    if hasattr(step, "reps") and step.reps:
        parts.append(f"reps={step.reps}")
    if hasattr(step, "weight") and step.weight:
        parts.append(f"weight={step.weight}lbs")
    if hasattr(step, "zone") and step.zone:
        parts.append(f"zone={step.zone}")
    if hasattr(step, "pace") and step.pace:
        parts.append(f"pace={step.pace['min']}-{step.pace['max']}")
    if hasattr(step, "hr") and step.hr:
        parts.append(f"hr={step.hr['min']}-{step.hr['max']}")
    if hasattr(step, "power") and step.power:
        parts.append(f"power={step.power['min']}-{step.power['max']}W")
    if hasattr(step, "power_pct") and step.power_pct:
        parts.append(f"power={step.power_pct['min']}-{step.power_pct['max']}%FTP")
    if hasattr(step, "notes") and step.notes:
        parts.append(f'"{step.notes}"')

    return " | ".join(parts)


def _format_workout() -> str:
    """Format the current workout as a readable summary."""
    w = _require_workout()
    lines = [f"Workout: {w.name}", f"Type: {w.type.value}", ""]
    if w.description:
        lines.append(f"Description: {w.description}")
        lines.append("")
    lines.append("Steps:")
    for i, step in enumerate(w.steps, 1):
        lines.append(f"  {i}. {_format_step(step, indent=0)}")
    return "\n".join(lines)


def _confirm_added(step: Any) -> str:
    """One-line confirmation of an added step (get_workout shows the full plan)."""
    n = len(_active_step_list())
    where = f"circuit (depth {len(_circuit_stack)})" if _circuit_stack else "workout"
    return f"Added to {where} as step {n}: {_format_step(step)}"


# ---------------------------------------------------------------------------
# Workout CRUD tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_workout(name: str, type: str) -> str:
    """Create a new workout.

    Args:
        name: Workout name (e.g. "Hyrox Race Sim")
        type: Sport type — one of: running, cycling, strength, swimming, walking, hiking
    """
    global _workout, _circuit_stack
    _circuit_stack = []
    _workout = Workout(name=name, type=SportType(type), steps=[])
    return f"Created workout '{name}' ({type}). Add steps, then preview_upload."


@mcp.tool()
def set_workout_name(name: str) -> str:
    """Rename the current workout.

    Args:
        name: New workout name
    """
    w = _require_workout()
    w.name = name
    return f"Renamed workout to '{name}'."


@mcp.tool()
def get_workout() -> str:
    """Get the current workout summary."""
    return _format_workout()


@mcp.tool()
def clear_workout() -> str:
    """Clear the current workout and start fresh."""
    global _workout, _circuit_stack
    _workout = None
    _circuit_stack = []
    return "Workout cleared."


# ---------------------------------------------------------------------------
# Step tools
# ---------------------------------------------------------------------------


@mcp.tool()
def add_warmup(
    duration: str = "lap",
    zone: str | None = None,
    exercise: str | None = None,
    notes: str | None = None,
) -> str:
    """Add a warmup step.

    Args:
        duration: Duration as "M:SS" or "lap" for lap button. Default: lap.
        zone: Training zone name (e.g. "easy", "z2").
        exercise: Exercise name for strength warmups (e.g. "rowing_machine").
        notes: Notes to display on the watch.
    """
    step = WarmupStep(duration=duration, zone=zone, exercise=exercise, notes=notes)
    _active_step_list().append(step)
    return _confirm_added(step)


@mcp.tool()
def add_cooldown(
    duration: str = "lap",
    zone: str | None = None,
    exercise: str | None = None,
    notes: str | None = None,
) -> str:
    """Add a cooldown step.

    Args:
        duration: Duration as "M:SS" or "lap" for lap button. Default: lap.
        zone: Training zone name (e.g. "easy", "z1").
        exercise: Exercise name for strength cooldowns (e.g. "rowing_machine").
        notes: Notes to display on the watch.
    """
    step = CooldownStep(duration=duration, zone=zone, exercise=exercise, notes=notes)
    _active_step_list().append(step)
    return _confirm_added(step)


@mcp.tool()
def add_run(
    duration: str | None = None,
    distance: str | None = None,
    zone: str | None = None,
    pace_min: str | None = None,
    pace_max: str | None = None,
    hr_min: float | None = None,
    hr_max: float | None = None,
    notes: str | None = None,
) -> str:
    """Add a running interval step.

    End condition: set exactly one of duration, distance, or neither (lap button).
    Target: set zone OR pace range OR HR range OR none.

    Args:
        duration: Duration as "M:SS" or "lap" for lap button.
        distance: Distance like "1km", "400m", "1mi".
        zone: Training zone name (e.g. "threshold", "tempo", "z4").
        pace_min: Faster pace bound (e.g. "6:25/mi"). Must pair with pace_max.
        pace_max: Slower pace bound (e.g. "6:40/mi"). Must pair with pace_min.
        hr_min: Lower HR bound in bpm. Must pair with hr_max.
        hr_max: Upper HR bound in bpm. Must pair with hr_min.
        notes: Notes to display on the watch.
    """
    pace = None
    if pace_min and pace_max:
        pace = {"min": pace_min, "max": pace_max}

    hr = None
    if hr_min is not None and hr_max is not None:
        hr = {"min": hr_min, "max": hr_max}

    step = RunStep(
        duration=duration,
        distance=distance,
        zone=zone,
        pace=pace,
        hr=hr,
        notes=notes,
    )
    _active_step_list().append(step)
    return _confirm_added(step)


@mcp.tool()
def add_bike(
    duration: str | None = None,
    distance: str | None = None,
    zone: str | None = None,
    power_min: float | None = None,
    power_max: float | None = None,
    power_pct_min: float | None = None,
    power_pct_max: float | None = None,
) -> str:
    """Add a cycling interval step.

    End condition: set exactly one of duration, distance, or neither (lap button).
    Target: set zone OR power range OR power_pct range OR none.

    Args:
        duration: Duration as "M:SS" or "lap" for lap button.
        distance: Distance like "10km", "20mi".
        zone: Training zone name (e.g. "threshold", "z3").
        power_min: Lower power in watts. Must pair with power_max.
        power_max: Upper power in watts. Must pair with power_min.
        power_pct_min: Lower power as %FTP. Must pair with power_pct_max.
        power_pct_max: Upper power as %FTP. Must pair with power_pct_min.
    """
    power = None
    if power_min is not None and power_max is not None:
        power = {"min": power_min, "max": power_max}

    power_pct = None
    if power_pct_min is not None and power_pct_max is not None:
        power_pct = {"min": power_pct_min, "max": power_pct_max}

    step = BikeStep(
        duration=duration,
        distance=distance,
        zone=zone,
        power=power,
        power_pct=power_pct,
    )
    _active_step_list().append(step)
    return _confirm_added(step)


@mcp.tool()
def add_exercise(
    exercise: str,
    duration: str | None = None,
    reps: int | None = None,
    weight: float | None = None,
    notes: str | None = None,
) -> str:
    """Add a strength/cardio exercise step.

    End condition: set reps OR duration OR neither (lap button).

    Args:
        exercise: Exercise name (e.g. "wall_ball", "kettlebell_swing", "burpee").
            Use list_exercises to see all available exercises.
        duration: Duration as "M:SS" or "lap" for lap button.
        reps: Number of repetitions.
        weight: Weight in lbs.
        notes: Notes to display on the watch (e.g. distance for carries).
    """
    step = ExerciseStep(
        exercise=exercise,
        duration=duration,
        reps=reps,
        weight=weight,
        notes=notes,
    )
    _active_step_list().append(step)
    return _confirm_added(step)


@mcp.tool()
def add_rest(duration: str) -> str:
    """Add a rest step.

    Args:
        duration: Rest duration as "M:SS" (e.g. "2:00" for 2 minutes).
    """
    step = RestStep(duration=duration)
    _active_step_list().append(step)
    return _confirm_added(step)


@mcp.tool()
def add_recovery(
    duration: str = "lap",
    distance: str | None = None,
    zone: str | None = None,
) -> str:
    """Add a recovery step between intervals.

    Args:
        duration: Duration as "M:SS" or "lap" for lap button. Default: lap.
        distance: Distance like "200m" for recovery jogs.
        zone: Training zone name (e.g. "z1", "easy").
    """
    step = RecoveryStep(duration=duration, distance=distance, zone=zone)
    _active_step_list().append(step)
    return _confirm_added(step)


@mcp.tool()
def add_circuit(iterations: int, skip_last_rest: bool | None = None) -> str:
    """Open a new circuit/repeat group. Steps added after this go inside the circuit.

    Call end_circuit when done adding steps to close it.

    Args:
        iterations: Number of times to repeat the circuit.
        skip_last_rest: If true, skip the last rest step in the circuit.
    """
    circuit = CircuitGroup(
        iterations=iterations,
        skip_last_rest=skip_last_rest,
        steps=[],
    )
    _active_step_list().append(circuit)
    _circuit_stack.append(circuit.steps)
    return f"Circuit x{iterations} opened. Add steps, then call end_circuit."


@mcp.tool()
def end_circuit() -> str:
    """Close the current circuit. Subsequent steps will be added at the parent level."""
    if not _circuit_stack:
        return "Error: No open circuit to close."
    closed = _circuit_stack.pop()
    return (
        f"Circuit closed ({len(closed)} steps). "
        "Next steps go to the "
        + (f"enclosing circuit (depth {len(_circuit_stack)})." if _circuit_stack else "workout.")
    )


@mcp.tool()
def remove_step(index: int) -> str:
    """Remove a step by its 1-based index from the top-level step list.

    Args:
        index: 1-based step index (as shown in get_workout output).
    """
    w = _require_workout()
    if index < 1 or index > len(w.steps):
        return f"Error: Invalid index {index}. Workout has {len(w.steps)} steps."
    removed = w.steps.pop(index - 1)
    return f"Removed step {index} ({removed.step_kind}).\n\n{_format_workout()}"


# ---------------------------------------------------------------------------
# Garmin Connect tools
# ---------------------------------------------------------------------------


@mcp.tool()
def preview_upload() -> str:
    """Preview the workout that would be uploaded to Garmin Connect.

    Always call this before upload_workout so the user can review and confirm.
    """
    w = _require_workout()
    zone_config = _get_zone_config()
    compiled = compile_workout(w, zone_config)
    steps = compiled["workoutSegments"][0]["workoutSteps"]
    sport = compiled["sportType"]["sportTypeKey"]

    lines = [
        "Ready to upload to Garmin Connect:",
        "",
        f"  Name: {w.name}",
        f"  Sport: {sport}",
        f"  Steps: {len(steps)}",
        "",
        _format_workout(),
        "",
        "Ask the user to confirm before calling upload_workout.",
    ]
    return "\n".join(lines)


@mcp.tool()
def upload_workout(confirm: bool, schedule_date: str | None = None) -> str:
    """Upload the current workout to Garmin Connect.

    IMPORTANT: Always call preview_upload first and get explicit user confirmation
    before calling this tool. Replaces any existing workout with the same name.

    Args:
        confirm: Must be true. Confirms the user has approved the upload.
        schedule_date: Optional date to schedule the workout (YYYY-MM-DD).
    """
    if not confirm:
        return "Upload cancelled. Call preview_upload to review the workout first."

    w = _require_workout()
    zone_config = _get_zone_config()
    compiled = compile_workout(w, zone_config)

    sync = GarminSync()
    sync.login()
    workout_id = sync.sync_workout(compiled, schedule_date)

    result = f"Uploaded '{w.name}' to Garmin Connect (ID: {workout_id})"
    if schedule_date:
        result += f"\nScheduled for {schedule_date}"
    return result


@mcp.tool()
def list_workouts() -> str:
    """List all workouts on Garmin Connect."""
    sync = GarminSync()
    sync.login()
    workouts = sync.list_workouts()

    if not workouts:
        return "No workouts found on Garmin Connect."

    lines = ["Workouts on Garmin Connect:", ""]
    for w in workouts:
        sport = w.get("sportType", {}).get("sportTypeKey", "unknown")
        lines.append(f"  ID: {w['workoutId']} | {w['workoutName']} ({sport})")
    return "\n".join(lines)


@mcp.tool()
def get_workout_details(workout_id: int) -> str:
    """Get the full structure of a workout by its Garmin ID.

    Returns the complete workout JSON (all steps, circuits, targets) as compact
    JSON with null/empty fields omitted. Use list_workouts to find workout IDs.

    Args:
        workout_id: Garmin workout ID (from list_workouts).
    """
    sync = GarminSync()
    sync.login()
    try:
        workout = sync.client.get_workout_by_id(workout_id)
        return json.dumps(_strip_empty(workout), separators=(",", ":"), default=str)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def delete_workout(workout_id: int) -> str:
    """Delete a workout from Garmin Connect.

    Args:
        workout_id: Garmin workout ID (from list_workouts).
    """
    sync = GarminSync()
    sync.login()
    sync.delete(workout_id)
    return f"Deleted workout {workout_id}."


# ---------------------------------------------------------------------------
# Reference tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_exercises(filter: str | None = None) -> str:
    """List available exercises for strength/cardio workouts.

    Args:
        filter: Optional filter string to search by name or category.
    """
    exercises = sorted(EXERCISE_REGISTRY.items())
    if filter:
        f = filter.lower()
        exercises = [(k, v) for k, v in exercises if f in k or f in v.category.lower()]

    if not exercises:
        return f"No exercises matching '{filter}'."

    lines = ["Available exercises:", ""]
    by_category: dict[str, list[str]] = {}
    for name, defn in exercises:
        by_category.setdefault(defn.category, []).append(name)

    for category in sorted(by_category):
        names = ", ".join(sorted(by_category[category]))
        lines.append(f"  {category}: {names}")

    return "\n".join(lines)


@mcp.tool()
def get_zones(sport_type: str | None = None) -> str:
    """Show available training zones from zones.yaml.

    Args:
        sport_type: Filter by sport — "running" or "cycling". Shows all if omitted.
    """
    config = _get_zone_config()
    if config is None:
        return "No zones.yaml found. Place it at workouts/zones.yaml."

    lines = ["Training Zones:", ""]

    if sport_type is None or sport_type == "running":
        lines.append("Running — Pace Zones:")
        for name, zone in config.running.pace_zones.items():
            lines.append(f"  {name}: {zone.min} - {zone.max}")
        lines.append("")
        lines.append("Running — HR Zones:")
        for name, zone in config.running.hr_zones.items():
            lines.append(f"  {name}: {zone.min} - {zone.max} bpm")
        lines.append("")

    if sport_type is None or sport_type == "cycling":
        lines.append(f"Cycling — Power Zones (FTP: {config.cycling.ftp}W):")
        for name, zone in config.cycling.power_zones.items():
            low = config.cycling.ftp * zone.min_pct / 100
            high = config.cycling.ftp * zone.max_pct / 100
            lines.append(f"  {name}: {zone.min_pct}-{zone.max_pct}% FTP ({low:.0f}-{high:.0f}W)")

    return "\n".join(lines)


@mcp.tool()
def validate_workout() -> str:
    """Compile the current workout and return the Garmin API JSON for inspection
    (compact; null/empty fields omitted)."""
    w = _require_workout()
    zone_config = _get_zone_config()
    compiled = compile_workout(w, zone_config)
    return json.dumps(_strip_empty(compiled), separators=(",", ":"))


@mcp.tool()
def save_yaml(path: str | None = None) -> str:
    """Save the current workout as a YAML template file.

    Args:
        path: File path to save to. Defaults to workouts/templates/<workout-name>.yaml.
    """
    import yaml

    w = _require_workout()
    if path is None:
        slug = w.name.lower().replace(" ", "-")
        path = f"workouts/templates/{slug}.yaml"

    def _step_to_dict(step: Any) -> dict:
        if isinstance(step, CircuitGroup):
            d: dict[str, Any] = {"circuit": step.iterations, "steps": []}
            for s in step.steps:
                d["steps"].append(_step_to_dict(s))
            if step.skip_last_rest is not None:
                d["skip_last_rest"] = step.skip_last_rest
            return d

        kind = step.step_kind
        fields = {}
        for field_name in step.model_fields:
            if field_name == "step_kind":
                continue
            val = getattr(step, field_name)
            if val is not None:
                fields[field_name] = val
        return {kind: fields} if fields else {kind: {}}

    data = {
        "name": w.name,
        "type": w.type.value,
        "steps": [_step_to_dict(s) for s in w.steps],
    }
    if w.description:
        data["description"] = w.description

    save_path = Path(path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return f"Saved workout to {save_path}"


@mcp.tool()
def load_template(path: str) -> str:
    """Load a workout from a YAML template file.

    Args:
        path: Path to the YAML file (e.g. "workouts/templates/hyrox-sim.yaml").
    """
    global _workout, _circuit_stack
    _circuit_stack = []
    _workout = load_workout(Path(path))
    return _format_workout()


@mcp.tool()
def list_templates() -> str:
    """List available YAML workout templates in workouts/templates/."""
    templates_dir = Path.cwd() / "workouts" / "templates"
    if not templates_dir.exists():
        return "No templates directory found."

    files = sorted(templates_dir.glob("*.yaml"))
    if not files:
        return "No templates found."

    lines = ["Available templates:", ""]
    for f in files:
        lines.append(f"  {f.relative_to(Path.cwd())}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


# ===========================================================================
# Data query tools (merged from garmin-connect-mcp)
# ===========================================================================

mcp.tool()(query_activities)
mcp.tool()(get_activity_details)
mcp.tool()(query_health_summary)
mcp.tool()(query_sleep_data)
mcp.tool()(query_heart_rate_data)
mcp.tool()(query_activity_metrics)
mcp.tool()(analyze_training_period)
mcp.tool()(compare_activities)
mcp.tool()(get_performance_metrics)
mcp.tool()(get_training_effect)
mcp.tool()(get_user_profile)
mcp.tool()(query_goals_and_records)
mcp.tool()(query_calendar_events)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

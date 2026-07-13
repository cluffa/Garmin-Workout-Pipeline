"""Tests for MCP workout-builder tool responses."""

import pytest

from garmin_pipeline import mcp_server


@pytest.fixture(autouse=True)
def fresh_state():
    mcp_server.clear_workout()
    yield
    mcp_server.clear_workout()


def test_create_workout_returns_short_confirmation():
    out = mcp_server.create_workout("Hyrox Race Sim", "running")
    assert "Hyrox Race Sim" in out
    assert len(out) < 200


def test_add_step_returns_one_line_not_full_workout():
    mcp_server.create_workout("Test", "running")
    mcp_server.add_warmup(duration="10:00", zone="easy")
    out = mcp_server.add_run(distance="1km", zone="threshold")
    assert out == "Added to workout as step 2: RUN | distance=1km | zone=threshold"
    # The full workout is NOT echoed back on every add
    assert "Workout: Test" not in out


def test_add_inside_circuit_reports_depth():
    mcp_server.create_workout("Circuits", "strength")
    mcp_server.add_circuit(iterations=3)
    out = mcp_server.add_exercise("burpee", reps=10)
    assert out.startswith("Added to circuit (depth 1) as step 1:")
    out = mcp_server.end_circuit()
    assert out == "Circuit closed (1 steps). Next steps go to the workout."


def test_get_workout_still_shows_full_structure():
    mcp_server.create_workout("Full View", "running")
    mcp_server.add_warmup(duration="10:00")
    mcp_server.add_run(distance="1km", zone="threshold")
    out = mcp_server.get_workout()
    assert "Workout: Full View" in out
    assert "WARMUP" in out
    assert "RUN" in out


def test_remove_step_shows_updated_workout():
    mcp_server.create_workout("Trim", "running")
    mcp_server.add_warmup(duration="10:00")
    mcp_server.add_run(distance="1km")
    out = mcp_server.remove_step(1)
    assert "Removed step 1" in out
    assert "Workout: Trim" in out


def test_set_workout_name_short():
    mcp_server.create_workout("Old", "running")
    assert mcp_server.set_workout_name("New") == "Renamed workout to 'New'."


def test_validate_workout_is_compact_json():
    import json

    mcp_server.create_workout("Compile Me", "running")
    mcp_server.add_run(distance="1km")
    out = mcp_server.validate_workout()
    compiled = json.loads(out)
    assert compiled["workoutName"] == "Compile Me"
    assert "\n" not in out
    assert '": ' not in out  # no pretty-print separators

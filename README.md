# Garmin Workout Pipeline

**One MCP server. 38 tools. Full Garmin Connect control.**

Define structured workouts in YAML or plain English, push them to your watch,
and query your entire training history — all through a single MCP server.

Built by merging two projects:

| Original | Author | Role |
|---|---|---|
| [Garmin-Workout-Pipeline](https://github.com/k-schmidt/Garmin-Workout-Pipeline) | [Kyle Schmidt](https://github.com/k-schmidt) | Workout builder, compiler, YAML templates, MCP server |
| [garmin-connect-mcp](https://pypi.org/project/garmin-connect-mcp/) | (PyPI) | Activity queries, health data, training analysis |

Both are built on Kyle's [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) Python library.

> **"Build me a Hyrox sim with 8 stations and 1km runs between each."**
>
> Then: **"How's my training looking this month?"**
>
> Same MCP server. Same conversation.

---

## Quickstart

```bash
# Install with uv (zero setup — resolves deps from lockfile)
uv tool install git+https://github.com/cluffa/Garmin-Workout-Pipeline.git

# Or clone and sync
git clone https://github.com/cluffa/Garmin-Workout-Pipeline.git
cd Garmin-Workout-Pipeline
uv sync

# Set credentials
export GARMIN_EMAIL=you@example.com
export GARMIN_PASSWORD=your-password

# Launch the MCP server
garmin-mcp
```

---

## MCP Configuration

### pi / Claude Code

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/cluffa/Garmin-Workout-Pipeline.git", "garmin-mcp"],
      "env": {
        "GARMIN_EMAIL": "you@example.com",
        "GARMIN_PASSWORD": "your-password"
      },
      "type": "stdio",
      "directTools": true
    }
  }
}
```

With `uvx`, there's no install step — uv fetches, caches, and runs in one shot.

### Claude Desktop

```json
{
  "mcpServers": {
    "garmin": {
      "command": "garmin-mcp",
      "env": {
        "GARMIN_EMAIL": "you@example.com",
        "GARMIN_PASSWORD": "your-password"
      }
    }
  }
}
```

---

## Tools — 38 total

### Workout Builder (24 tools)

| Category | Tools |
|---|---|
| **Workout CRUD** | `create_workout`, `get_workout`, `set_workout_name`, `clear_workout` |
| **Steps** | `add_warmup`, `add_cooldown`, `add_run`, `add_bike`, `add_exercise`, `add_rest`, `add_recovery`, `remove_step` |
| **Circuits** | `add_circuit`, `end_circuit` |
| **Upload & Sync** | `preview_upload`, `upload_workout`, `list_workouts`, `delete_workout` |
| **Workout Data** | `get_workout_details` — read full workout JSON from Garmin |
| **Templates** | `save_yaml`, `load_template`, `list_templates` |
| **Reference** | `list_exercises`, `get_zones`, `validate_workout` |

### Data & Analysis (14 tools)

| Category | Tools |
|---|---|
| **Activities** | `query_activities` — list/search with pagination · `get_activity_details` — splits, weather, HR zones |
| **Health** | `query_health_summary` — stats, body battery, readiness · `query_sleep_data` · `query_heart_rate_data` · `query_activity_metrics` — steps, stress, SpO2 |
| **Training** | `analyze_training_period` — volume, trends, type breakdown · `get_performance_metrics` — VO2 max, HRV, hill/endurance scores · `get_training_effect` · `compare_activities` |
| **Profile** | `get_user_profile` — name, stats, PRs, devices · `query_goals_and_records` — goals, PRs, race predictions |
| **Calendar** | `query_calendar_events` — races, scheduled workouts, training plan events |

Responses are token-optimized for LLM context: compact JSON, null fields omitted, curated
summaries instead of raw Garmin payloads, and intraday time series (per-minute HR, sleep
movement, stress arrays, ...) summarized into daily stats. Pass `raw=true` to any query tool
to get the complete unabridged Garmin payload when you need it.

---

## Architecture

```
pi / Claude ──MCP (stdio)──▶ garmin-mcp
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              mcp_server.py  tools/*.py  compiler.py
                    │           │           │
                    └───────────┼───────────┘
                                │
                          GarminClient
                          (safe_call wrapper)
                                │
                          garminconnect
                          (Kyle Schmidt)
                                │
                         Garmin Connect API
```

**Key design decisions:**

- **Single process** — was two MCP servers (workout builder + data queries). Merged into one to eliminate duplicate auth, reduce resource usage, and unify tool naming.
- **Synchronous** — all tools are sync (no `async`/`await`). FastMCP handles concurrency. Simpler to write, debug, and test.
- **Shared client** — `GarminClient` wraps `GarminSync` with a `safe_call()` method that lazily authenticates and handles errors (rate limits, auth expiry, 404s).
- **Effort-based** — zones are optional. Workouts can prescribe by pace, HR, power, or effort anchors. The compiler resolves zone names to Garmin API target values.

---

## Workout Types

### Running

```yaml
name: "Threshold Intervals"
type: running
steps:
  - warmup: { duration: "10:00", zone: easy }
  - run: { distance: "1km", pace: { min: "6:25/mi", max: "6:40/mi" } }
  - recovery: { duration: "2:00" }
  - run: { duration: "5:00", zone: threshold }
  - cooldown: { duration: lap, zone: easy }
```

### Cycling

```yaml
name: "Sweet Spot"
type: cycling
steps:
  - warmup: { duration: "10:00", zone: z2 }
  - bike: { duration: "20:00", zone: threshold }
  - cooldown: { duration: "5:00" }
```

### Strength / Cardio

84 exercises with rep counts, weights, and circuit support.

```yaml
name: "Hyrox Strength"
type: strength
steps:
  - warmup: { duration: lap, exercise: rowing_machine }
  - circuit:
      iterations: 4
      steps:
        - exercise: { exercise: wall_ball, reps: 20, weight: 13 }
        - exercise: { exercise: weighted_lunge, reps: 20, weight: 45 }
        - rest: { duration: "2:00" }
  - cooldown: { duration: lap, exercise: rowing_machine }
```

---

## Step Reference

| Type | End Conditions | Targets |
|---|---|---|
| `warmup` | duration, lap | zone, exercise |
| `cooldown` | duration, lap | zone, exercise |
| `run` | duration, distance, lap | zone, pace, hr |
| `bike` | duration, distance, lap | zone, power, power_pct |
| `recovery` | duration, distance, lap | zone |
| `exercise` | duration, reps, lap | — |
| `rest` | duration | — |
| `circuit` | iterations | nested steps |

---

## Zones

Define training zones once in `workouts/zones.yaml` with HR, pace, and power targets per sport:

```yaml
running:
  pace_zones:
    easy: { min: "9:00/mi", max: "10:30/mi" }
    threshold: { min: "6:25/mi", max: "6:50/mi" }
  hr_zones:
    z1: { min: 110, max: 130 }
    z2: { min: 130, max: 150 }
    z3: { min: 150, max: 165 }
    z4: { min: 165, max: 180 }
    z5: { min: 180, max: 200 }

cycling:
  ftp: 250
  power_zones:
    z2: { min_pct: 55, max_pct: 75 }
    threshold: { min_pct: 90, max_pct: 105 }
```

The compiler resolves names (like `threshold`, `z2`) to numeric Garmin API targets.

---

## CLI

```bash
gwp push <file> --zones <zones.yaml>              # Upload workout
gwp push <file> --zones <zones.yaml> --schedule 2026-07-25  # Upload + schedule
gwp push <file> --zones <zones.yaml> --dry-run     # Preview JSON
gwp validate <file> --zones <zones.yaml>            # Compile and validate
gwp list                                            # List workouts on Garmin
gwp delete <workout-id>                             # Delete a workout
gwp zones --zones <zones.yaml>                      # Show resolved zones
```

---

## Project Structure

```
garmin_pipeline/
├── mcp_server.py          # MCP server — 38 tools, entry point
├── cli.py                 # CLI (gwp command)
├── client.py              # GarminClient wrapper with safe_call()
├── compiler.py            # Workout model → Garmin API JSON
├── exercises.py           # 84+ exercise name → Garmin category registry
├── loader.py              # YAML template parser
├── models.py              # Pydantic step models
├── sync.py                # Garmin Connect auth, upload, schedule, delete
├── zones.py               # Zone resolution (HR, pace, power)
├── time_utils.py          # Date parsing, range helpers
├── pull.py                # Pull workouts from Garmin Connect
└── tools/
    ├── activities.py      # query_activities, get_activity_details
    ├── calendar.py        # query_calendar_events — races, scheduled workouts
    ├── health.py          # query_health_summary, sleep, HR, metrics
    ├── profile.py         # get_user_profile, query_goals_and_records
    └── training.py        # analyze_training_period, performance, compare
```

---

## Development

```bash
uv sync --dev          # install with dev dependencies
uv run pytest -v       # run tests
uv run ruff check .    # lint
uv run mypy .          # type check
```

---

## License

[MIT](LICENSE) — © Kyle Schmidt (original Garmin-Workout-Pipeline), with data tools adapted from garmin-connect-mcp.

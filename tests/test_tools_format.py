"""Tests for MCP data-tool response formatting and curation."""

import json

from garmin_pipeline.tools._format import (
    fmt_dur,
    fmt_pace,
    project_activity,
    scalars_of,
    strip_empty,
    to_json,
)
from garmin_pipeline.tools.health import (
    _curate_body_battery,
    _curate_floors,
    _curate_heart_rate,
    _curate_sleep,
    _curate_steps,
    _curate_summary,
    _curate_training_status,
)
from garmin_pipeline.tools.profile import _curate_device

# ---------------------------------------------------------------------------
# strip_empty / to_json
# ---------------------------------------------------------------------------


def test_strip_empty_drops_nulls_keeps_zeros():
    obj = {"a": None, "b": 0, "c": False, "d": "", "e": [], "f": {}, "g": "x"}
    assert strip_empty(obj) == {"b": 0, "c": False, "g": "x"}


def test_strip_empty_nested():
    obj = {"outer": {"inner": None, "keep": 1}, "gone": {"only": None}}
    assert strip_empty(obj) == {"outer": {"keep": 1}}


def test_strip_empty_preserves_list_elements():
    obj = {"lst": [1, None, {"a": None, "b": 2}]}
    assert strip_empty(obj) == {"lst": [1, None, {"b": 2}]}


def test_to_json_is_compact():
    s = to_json({"a": 1, "b": [1, 2], "c": None})
    assert s == '{"a":1,"b":[1,2]}'


def test_scalars_of_drops_containers():
    d = {"a": 1, "b": "x", "c": [1, 2], "d": {"k": 1}, "e": 2.5}
    assert scalars_of(d) == {"a": 1, "b": "x", "e": 2.5}


# ---------------------------------------------------------------------------
# Activity projection
# ---------------------------------------------------------------------------

LIST_ACTIVITY = {
    "activityId": 123,
    "activityName": "Morning Run",
    "startTimeLocal": "2026-07-12 07:01:00",
    "startTimeGMT": "2026-07-12 11:01:00",
    "activityType": {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
    "eventType": {"typeId": 9, "typeKey": "uncategorized"},
    "distance": 8046.7,
    "duration": 2705.2,
    "movingDuration": 2650.0,
    "elevationGain": 45.0,
    "elevationLoss": 44.0,
    "averageSpeed": 2.975,
    "maxSpeed": 4.1,
    "averageHR": 152.0,
    "maxHR": 171.0,
    "calories": 520.0,
    "averageRunningCadenceInStepsPerMinute": 172.0,
    "steps": 7620,
    "aerobicTrainingEffect": 3.1,
    "anaerobicTrainingEffect": 0.2,
    "activityTrainingLoad": 145.7,
    "vO2MaxValue": 51.0,
    "deviceId": 3999999999,
    "manufacturer": "GARMIN",
    "lapCount": 6,
    "privacy": {"typeId": 2, "typeKey": "private"},
    "ownerId": 99,
    "ownerFullName": "Alex",
    "ownerProfileImageUrlSmall": "https://example.com/s.jpg",
    "ownerProfileImageUrlMedium": "https://example.com/m.jpg",
    "ownerProfileImageUrlLarge": "https://example.com/l.jpg",
    "hasPolyline": True,
    "hasSplits": True,
    "beginTimestamp": 1783938060000,
}


def test_project_activity_list_shape():
    p = project_activity(LIST_ACTIVITY, "metric")
    assert p["id"] == 123
    assert p["name"] == "Morning Run"
    assert p["type"] == "running"
    assert p["start"] == "2026-07-12 07:01:00"
    assert p["distance_m"] == 8047
    assert p["duration_s"] == 2705
    assert p["moving_s"] == 2650
    assert p["avg_hr"] == 152
    assert p["max_hr"] == 171
    assert p["pace"] == "5:36/km"
    assert p["avg_cadence"] == 172
    assert p["training_load"] == 145.7
    assert p["vo2max"] == 51.0
    # Noise fields must not leak through
    for junk in ("ownerProfileImageUrlLarge", "deviceId", "privacy", "beginTimestamp"):
        assert junk not in p
    assert "ownerProfileImageUrlLarge" not in json.dumps(p)


def test_project_activity_dto_shape():
    dto = {
        "activityId": 456,
        "activityName": "Tempo Ride",
        "activityTypeDTO": {"typeKey": "cycling"},
        "locationName": "Columbus",
        "summaryDTO": {
            "startTimeLocal": "2026-07-10T06:30:00.0",
            "distance": 40000.0,
            "duration": 4800.0,
            "elevationGain": 210.0,
            "averageSpeed": 8.333,
            "averageHR": 141.0,
            "maxHR": 165.0,
            "averagePower": 210.0,
            "maxPower": 480.0,
            "normalizedPower": 221.0,
            "calories": 900.0,
            "trainingEffect": 3.5,
            "anaerobicTrainingEffect": 1.1,
            "activityTrainingLoad": 180.0,
        },
    }
    p = project_activity(dto, "metric")
    assert p["id"] == 456
    assert p["type"] == "cycling"
    assert p["start"] == "2026-07-10T06:30:00.0"
    assert p["distance_m"] == 40000
    assert p["avg_power_w"] == 210
    assert p["norm_power_w"] == 221
    assert p["speed"] == "30.00 km/h"
    assert p["aerobic_te"] == 3.5


def test_project_activity_imperial_pace():
    p = project_activity(LIST_ACTIVITY, "imperial")
    assert p["distance"] == "5.00 mi"
    assert p["pace"].endswith("/mi")


def test_fmt_helpers():
    assert fmt_dur(3725) == "1h 2m 5s"
    assert fmt_dur(65) == "1m 5s"
    assert fmt_pace(0, "metric") == "N/A"


# ---------------------------------------------------------------------------
# Health curation
# ---------------------------------------------------------------------------


def test_curate_summary_whitelists():
    summary = {
        "userProfileId": 99,
        "uuid": "abc-def",
        "totalKilocalories": 2900.0,
        "activeKilocalories": 700.0,
        "totalSteps": 12000,
        "dailyStepGoal": 10000,
        "restingHeartRate": 47,
        "minHeartRate": 44,
        "maxHeartRate": 168,
        "lastSevenDaysAvgRestingHeartRate": 48,
        "averageStressLevel": 28,
        "bodyBatteryHighestValue": 92,
        "bodyBatteryLowestValue": 21,
        "floorsAscended": 12.348,
        "rule": {"typeId": 2, "typeKey": "private"},
        "privacyProtected": False,
        "source": "GARMIN",
        "includesWellnessData": True,
        "durationInMilliseconds": 86400000,
    }
    c = _curate_summary(summary)
    assert c["totalSteps"] == 12000
    assert c["restingHeartRate"] == 47
    assert c["floorsAscended"] == 12.3
    for junk in ("uuid", "rule", "userProfileId", "durationInMilliseconds"):
        assert junk not in c


def test_curate_heart_rate_summarizes_series():
    hr = {
        "calendarDate": "2026-07-12",
        "restingHeartRate": 47,
        "minHeartRate": 44,
        "maxHeartRate": 152,
        "lastSevenDaysAvgRestingHeartRate": 48,
        "heartRateValueDescriptors": [
            {"key": "timestamp", "index": 0}, {"key": "heartrate", "index": 1}],
        "heartRateValues": [[1783938060000, 60], [1783938180000, 70],
                            [1783938300000, None], [1783938420000, 80]],
    }
    c = _curate_heart_rate(hr)
    assert c["restingHeartRate"] == 47
    assert c["averageHeartRate"] == 70  # (60+70+80)/3
    assert c["samples"] == 3
    assert "heartRateValues" not in c
    assert "heartRateValueDescriptors" not in c


def test_curate_sleep_keeps_summary_drops_series():
    sleep = {
        "dailySleepDTO": {
            "calendarDate": "2026-07-12",
            "sleepTimeSeconds": 27000,
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 14400,
            "remSleepSeconds": 6000,
            "awakeSleepSeconds": 1200,
            "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
            "averageSpO2Value": 95.0,
            "averageRespirationValue": 14.0,
            "avgSleepStress": 12.0,
            "sleepStartTimestampLocal": None,
        },
        "sleepMovement": [{"startGMT": "x", "endGMT": "y", "activityLevel": 1.2}] * 480,
        "sleepLevels": [{"startGMT": "x", "endGMT": "y", "activityLevel": 0}] * 100,
        "sleepHeartRate": [[1, 50]] * 400,
        "sleepStress": [[1, 10]] * 400,
        "hrvData": [[1, 55]] * 90,
        "avgOvernightHrv": 58.0,
        "hrvStatus": "BALANCED",
        "bodyBatteryChange": 55,
        "restingHeartRate": 47,
        "restlessMomentsCount": 21,
    }
    c = _curate_sleep(sleep)
    assert c["dailySleepDTO"]["sleepTimeSeconds"] == 27000
    assert c["dailySleepDTO"]["sleepScores"]["overall"]["value"] == 82
    assert c["avgOvernightHrv"] == 58.0
    assert c["bodyBatteryChange"] == 55
    for series in ("sleepMovement", "sleepLevels", "sleepHeartRate", "sleepStress", "hrvData"):
        assert series not in c
    # Curated output must be dramatically smaller than raw
    assert len(json.dumps(c)) < len(json.dumps(sleep)) / 10


def test_curate_steps_totals():
    chunks = [
        {"startGMT": "a", "endGMT": "b", "steps": 100, "primaryActivityLevel": "active"},
        {"startGMT": "c", "endGMT": "d", "steps": 50, "primaryActivityLevel": "sedentary"},
        {"startGMT": "e", "endGMT": "f", "steps": 200, "primaryActivityLevel": "active"},
    ]
    c = _curate_steps(chunks)
    assert c["total_steps"] == 350
    assert c["minutes_by_activity_level"] == {"active": 30, "sedentary": 15}


def test_curate_body_battery():
    bb = [{
        "date": "2026-07-12",
        "charged": 60,
        "drained": 55,
        "startTimestampGMT": "2026-07-12T04:00:00.0",
        "bodyBatteryValueDescriptorDTOList": [
            {"bodyBatteryValueDescriptorIndex": 0,
             "bodyBatteryValueDescriptorKey": "timestamp"},
            {"bodyBatteryValueDescriptorIndex": 1,
             "bodyBatteryValueDescriptorKey": "bodyBatteryStatus"},
            {"bodyBatteryValueDescriptorIndex": 2,
             "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
        ],
        "bodyBatteryValuesArray": [[1, "MEASURED", 40], [2, "MEASURED", 90], [3, "MEASURED", 35]],
    }]
    c = _curate_body_battery(bb)
    assert c[0]["charged"] == 60
    assert c[0]["highest"] == 90
    assert c[0]["lowest"] == 35
    assert c[0]["latest"] == 35
    assert "bodyBatteryValuesArray" not in c[0]


def test_curate_floors():
    floors = {
        "startTimestampGMT": "2026-07-12T04:00:00.0",
        "floorsValueDescriptorDTOList": [
            {"key": "startTimeGMT", "index": 0},
            {"key": "endTimeGMT", "index": 1},
            {"key": "floorsAscended", "index": 2},
            {"key": "floorsDescended", "index": 3},
        ],
        "floorValuesArray": [["a", "b", 2, 1], ["c", "d", 3.5, 0]],
    }
    c = _curate_floors(floors)
    assert c["floorsAscended"] == 5.5
    assert c["floorsDescended"] == 1
    assert "floorValuesArray" not in c


def test_curate_training_status_extracts_nested():
    ts = {
        "userId": 99,
        "mostRecentVO2Max": {
            "generic": {"vo2MaxValue": 51.0, "fitnessAge": 28, "calendarDate": "2026-07-10"},
            "cycling": None,
        },
        "mostRecentTrainingLoadBalance": {
            "metricsTrainingLoadBalanceDTOMap": {
                "3999999999": {"monthlyLoadAerobicLow": 250.0,
                    "monthlyLoadAerobicHigh": 300.0,
                    "trainingBalanceFeedbackPhrase": "BALANCED"},
            },
        },
        "mostRecentTrainingStatus": {
            "latestTrainingStatusData": {
                "3999999999": {"trainingStatus": 4,
                    "trainingStatusFeedbackPhrase": "PRODUCTIVE_1",
                    "fitnessTrend": 2,
                    "acuteTrainingLoadDTO": {"acwrStatus": "OPTIMAL",
                        "dailyTrainingLoadAcute": 500,
                        "dailyTrainingLoadChronic": 480,
                        "dailyAcuteChronicWorkloadRatio": 1.04},
                },
            },
        },
    }
    c = _curate_training_status(ts)
    assert c["vo2max"]["vo2MaxValue"] == 51.0
    assert c["training_load_balance"]["trainingBalanceFeedbackPhrase"] == "BALANCED"
    assert c["training_status"]["trainingStatusFeedbackPhrase"] == "PRODUCTIVE_1"
    assert c["training_status"]["acuteTrainingLoad"]["acwrStatus"] == "OPTIMAL"


def test_curate_device():
    dev = {
        "deviceId": 3999999999,
        "productDisplayName": "Forerunner 965",
        "softwareVersion": "20.26",
        "lastSyncTime": "2026-07-12T12:00:00.0",
        "primaryActivityTracker": True,
        "supportedCapabilities": [f"CAP_{i}" for i in range(200)],
        "deviceSettings": {"big": "blob"},
    }
    c = _curate_device(dev)
    assert c["productDisplayName"] == "Forerunner 965"
    assert "supportedCapabilities" not in c
    assert "deviceSettings" not in c


# ---------------------------------------------------------------------------
# Sleep DTO trimming
# ---------------------------------------------------------------------------


def test_curate_sleep_drops_need_and_alignment_trims_scores():
    sleep = {
        "dailySleepDTO": {
            "calendarDate": "2026-07-12",
            "sleepTimeSeconds": 27000,
            "sleepScores": {
                "overall": {"value": 82, "qualifierKey": "GOOD"},
                "deepPercentage": {"value": 23, "qualifierKey": "EXCELLENT",
                                   "optimalStart": 16.0, "optimalEnd": 33.0,
                                   "idealStartInSeconds": 4320.0},
            },
            "sleepNeed": {"baseline": 480, "actual": 480, "feedback": "x"},
            "nextSleepNeed": {"baseline": 480, "actual": 480},
            "sleepAlignment": {"status": "ALIGNED"},
        },
    }
    c = _curate_sleep(sleep)
    dto = c["dailySleepDTO"]
    for gone in ("sleepNeed", "nextSleepNeed", "sleepAlignment"):
        assert gone not in dto
    assert dto["sleepScores"]["deepPercentage"] == {
        "value": 23, "qualifierKey": "EXCELLENT"}


# ---------------------------------------------------------------------------
# Performance score trimming
# ---------------------------------------------------------------------------


def test_drop_series_keeps_scalars_drops_arrays():
    from garmin_pipeline.tools.training import _drop_series

    score = {
        "overallScore": 73,
        "strengthScore": 60,
        "enduranceScoreDTO": {"classification": 3, "feedbackPhrase": "TRAINED"},
        "weeklyScores": [{"week": 1, "score": 70}] * 26,
        "deviceMap": {"123": {"nested": {"deep": 1}}},
    }
    c = _drop_series(score)
    assert c["overallScore"] == 73
    assert c["enduranceScoreDTO"]["classification"] == 3
    assert "weeklyScores" not in c
    assert "deviceMap" not in c  # only nested containers inside -> dropped


# ---------------------------------------------------------------------------
# Daily briefing helpers
# ---------------------------------------------------------------------------

from garmin_pipeline.tools.briefing import (  # noqa: E402
    _load_window,
    _readiness_flags,
    _sleep_brief,
)


def _mk_sleep(duration_s=27000, score=82):
    return {
        "dailySleepDTO": {
            "calendarDate": "2026-07-13",
            "sleepTimeSeconds": duration_s,
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 14400,
            "remSleepSeconds": 6000,
            "awakeSleepSeconds": 1200,
            "avgOvernightHrv": 58.0,
            "sleepScores": {"overall": {"value": score, "qualifierKey": "GOOD"}},
        },
        "restingHeartRate": 47,
        "bodyBatteryChange": 55,
    }


def test_sleep_brief_compacts():
    b = _sleep_brief(_mk_sleep())
    assert b["duration_s"] == 27000
    assert b["score"] == 82
    assert b["resting_hr"] == 47
    assert b["body_battery_change"] == 55
    assert b["overnight_hrv"] == 58.0


def test_sleep_brief_none_when_no_sleep():
    assert _sleep_brief({"dailySleepDTO": {"calendarDate": "2026-07-13"}}) is None
    assert _sleep_brief(None) is None


def test_readiness_flags_all_clear():
    flags = _readiness_flags(
        sleep={"duration_s": 27000, "score": 82},
        hrv_summary={"lastNightAvg": 61, "status": "BALANCED",
                     "baseline": {"balancedLow": 47}},
        resting_hr=45, resting_hr_7d=44, body_battery_latest=90,
        readiness={"score": 78, "level": "HIGH"},
    )
    assert flags == []


def test_readiness_flags_fire():
    flags = _readiness_flags(
        sleep={"duration_s": 5 * 3600, "score": 50},
        hrv_summary={"lastNightAvg": 40, "status": "LOW",
                     "baseline": {"balancedLow": 47}},
        resting_hr=52, resting_hr_7d=44, body_battery_latest=25,
        readiness={"score": 20, "level": "LOW"},
    )
    assert "sleep_short" in flags
    assert "sleep_score_low" in flags
    assert "hrv_below_baseline" in flags
    assert "hrv_status_low" in flags
    assert "resting_hr_elevated" in flags
    assert "body_battery_low" in flags
    assert "training_readiness_low" in flags


def test_readiness_flags_handles_missing_data():
    assert _readiness_flags(None, None, None, None, None, None) == []


def test_load_window_aggregates_runs():
    acts = [
        {"startTimeLocal": "2026-07-12 07:00:00", "distance": 10000.0,
         "duration": 3600.0, "activityType": {"typeKey": "running"}},
        {"startTimeLocal": "2026-07-11 07:00:00", "distance": 20000.0,
         "duration": 7200.0, "activityType": {"typeKey": "trail_running"}},
        {"startTimeLocal": "2026-07-10 07:00:00", "distance": 30000.0,
         "duration": 5400.0, "activityType": {"typeKey": "cycling"}},
        {"startTimeLocal": "2026-06-01 07:00:00", "distance": 99999.0,
         "duration": 9999.0, "activityType": {"typeKey": "running"}},
    ]
    w = _load_window(acts, "2026-07-10", "2026-07-13", "metric")
    assert w["activities"] == 3
    assert w["distance_m"] == 60000
    assert w["run_distance"] == "30.00 km"


def test_curate_primary_device_drops_registered_devices():
    from garmin_pipeline.tools.profile import _curate_primary_device

    primary = {
        "PrimaryTrainingDevice": {"deviceId": 123},
        "WearableDevices": {"deviceWeights": [
            {"displayName": "fenix 8", "deviceId": 123, "weight": 100,
             "imageUrl": "https://example.com/huge.png"}],
            "wearableDeviceCount": 1},
        "RegisteredDevices": [{"deviceId": 123, "caps": ["x"] * 200}] * 2,
    }
    c = _curate_primary_device(primary)
    assert "RegisteredDevices" not in c
    assert c["PrimaryTrainingDevice"] == {"deviceId": 123}
    weights = c["WearableDevices"]["deviceWeights"]
    assert weights[0]["displayName"] == "fenix 8"
    assert "imageUrl" not in weights[0]


def test_curate_prs_labels_and_formats():
    from garmin_pipeline.tools.profile import _curate_prs

    prs = [
        {"id": 1, "typeId": 3, "activityId": 9, "activityName": "Parkrun",
         "actStartDateTimeInGMTFormatted": "2026-04-21T20:37:28.0",
         "activityStartDateTimeInGMT": 1776803848000,
         "value": 1380.96, "prTypeLabelKey": None, "poolLengthUnit": None},
        {"id": 2, "typeId": 7, "activityName": "Autumn Squatch 50K",
         "actStartDateTimeInGMTFormatted": "2025-11-02T12:00:00.0",
         "value": 53402.94},
        {"id": 3, "typeId": 31, "activityName": "Strength", "value": 124750.0},
    ]
    c = _curate_prs(prs)
    assert c[0] == {"typeId": 3, "label": "5K", "value": 1380.96,
                    "activity": "Parkrun", "date": "2026-04-21",
                    "time": "23:01"}
    assert c[1]["label"] == "Longest Run"
    assert c[1]["km"] == 53.4
    assert c[2]["label"] is None  # unknown ids pass through unlabeled
    assert c[2]["value"] == 124750.0


def test_agent_workout_tag():
    from garmin_pipeline.tools._format import (
        AGENT_TAG, is_agent_workout, tag_workout_name)

    assert is_agent_workout("\U0001f916 W3 Wed Intervals")
    assert is_agent_workout("  \U0001f916 padded")
    assert not is_agent_workout("BR Taper - Speed Tuning")
    assert not is_agent_workout(None)
    assert not is_agent_workout("")

    tagged = tag_workout_name("W3 Wed Intervals")
    assert tagged == f"{AGENT_TAG} W3 Wed Intervals"
    # Idempotent — never double-tags
    assert tag_workout_name(tagged) == tagged

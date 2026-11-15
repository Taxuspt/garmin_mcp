import pytest
from garmin_mcp.swimming import build_swim_json, pool_template
from garmin_mcp.workout_analysis import analyze_workout


def test_template_and_css():
    payload = pool_template()
    assert analyze_workout(payload)["totals"]["total_distance_m"] == 1000
    assert payload["poolLength"] == 25
    assert payload["sportType"]["sportTypeId"] == 4
    payload = build_swim_json("CSS", payload["workoutSegments"][0]["workoutSteps"], css_seconds_per_100m=120)
    assert payload["workoutSegments"][0]["workoutSteps"][0]["targetValueOne"] == pytest.approx(100/120)
    assert analyze_workout(payload)["totals"]["computed_duration_s"] == pytest.approx(1320)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_css(value):
    with pytest.raises(ValueError):
        build_swim_json("test", [{}], css_seconds_per_100m=value)

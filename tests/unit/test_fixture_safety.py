import json

import pytest

from garmin_mcp.fixture_safety import build_get_fixture


def test_fixture_recorder_refuses_writes():
    with pytest.raises(ValueError, match="Only GET"):
        build_get_fixture(
            method="POST",
            url="https://connect.garmin.com/workout",
            request_headers={},
            response_status=200,
            response_headers={},
            response_body={},
        )


def test_fixture_recorder_redacts_secrets_identity_names_and_location():
    fixture = build_get_fixture(
        method="GET",
        url="https://connect.garmin.com/activity?userId=123&limit=20",
        request_headers={"Authorization": "Bearer secret", "Cookie": "sid=secret"},
        response_status=200,
        response_headers={"Set-Cookie": "sid=new-secret"},
        response_body={
            "userProfileId": 123,
            "activityName": "Home loop",
            "startLatitude": 23.1,
            "startLongitude": 113.2,
            "summary": {"averageHR": 145},
        },
    )
    serialized = json.dumps(fixture)
    assert "Bearer secret" not in serialized
    assert "sid=secret" not in serialized
    assert "Home loop" not in serialized
    assert "23.1" not in serialized
    assert "113.2" not in serialized
    assert "userId=%3Credacted%3E" in fixture["request"]["url"]
    assert fixture["response"]["body"]["summary"]["averageHR"] == 145

"""
Integration tests for the courses module MCP tools.

Covers get_courses, upload_course, and delete_course using FastMCP integration
with a mocked Garmin client. No real Garmin account or network access is used.
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import courses
from garmin_mcp.courses import _build_course_payload, _haversine


@pytest.fixture
def app_with_courses(mock_garmin_client):
    """Create a FastMCP app with the courses tools registered."""
    courses.configure(mock_garmin_client)
    app = FastMCP("Test Courses")
    app = courses.register_tools(app)
    return app


def _result_text(result):
    """Extract the text payload from a FastMCP call_tool result."""
    return result[0][0].text


# --- get_courses ----------------------------------------------------------

@pytest.mark.asyncio
async def test_get_courses_curates_fields(app_with_courses, mock_garmin_client):
    """get_courses curates the raw Garmin list into a compact shape."""
    mock_garmin_client.client.connectapi.return_value = [
        {
            "courseId": 111,
            "courseName": "River Loop",
            "distanceInMeters": 10250.5,
            "elevationGainInMeters": 120.0,
            "elevationLossInMeters": 118.0,
            "activityType": {"typeKey": "running"},
            "hasPaceBand": False,
            "createdDateFormatted": "2024-03-01",
        }
    ]

    result = await app_with_courses.call_tool("get_courses", {})

    data = json.loads(_result_text(result))
    assert data["count"] == 1
    course = data["courses"][0]
    assert course["course_id"] == 111
    assert course["name"] == "River Loop"
    assert course["distance_m"] == 10250.5
    assert course["activity"] == "running"
    mock_garmin_client.client.connectapi.assert_called_once_with("/course-service/course")


@pytest.mark.asyncio
async def test_get_courses_empty(app_with_courses, mock_garmin_client):
    """An empty course list returns count 0 and an empty list."""
    mock_garmin_client.client.connectapi.return_value = []

    result = await app_with_courses.call_tool("get_courses", {})

    data = json.loads(_result_text(result))
    assert data["count"] == 0
    assert data["courses"] == []


@pytest.mark.asyncio
async def test_get_courses_error_is_caught(app_with_courses, mock_garmin_client):
    """A client error is surfaced as a clean message, not a traceback."""
    mock_garmin_client.client.connectapi.side_effect = Exception("boom")

    result = await app_with_courses.call_tool("get_courses", {})

    assert "Error listing courses" in _result_text(result)


# --- upload_course --------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_course_rejects_non_gpx(app_with_courses, mock_garmin_client):
    """Only .gpx files are accepted; nothing is uploaded otherwise."""
    result = await app_with_courses.call_tool(
        "upload_course", {"gpx_path": "/tmp/route.tcx"}
    )

    assert "only .gpx files are allowed" in _result_text(result)
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_upload_course_missing_file(app_with_courses, mock_garmin_client):
    """A non-existent .gpx path is reported without an API call."""
    result = await app_with_courses.call_tool(
        "upload_course", {"gpx_path": "/no/such/file.gpx"}
    )

    assert "GPX file not found" in _result_text(result)
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_upload_course_rejects_unknown_activity_type(
    app_with_courses, mock_garmin_client, tmp_path
):
    """An unsupported activity_type is rejected before uploading."""
    gpx = tmp_path / "route.gpx"
    gpx.write_text("<gpx></gpx>")

    result = await app_with_courses.call_tool(
        "upload_course",
        {"gpx_path": str(gpx), "activity_type": "swimming"},
    )

    assert "unknown activity_type" in _result_text(result)
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_upload_course_two_step_flow(app_with_courses, mock_garmin_client, tmp_path):
    """A valid GPX runs the import-then-create flow and returns the saved course."""
    gpx = tmp_path / "river_loop.gpx"
    gpx.write_text("<gpx></gpx>")  # parsed server-side (mocked below)

    parsed = {
        "courseName": "River Loop",
        "geoPoints": [
            {"latitude": 40.0, "longitude": -105.0, "elevation": 1600.0},
            {"latitude": 40.001, "longitude": -105.001, "elevation": 1605.0},
        ],
    }
    saved = {
        "courseId": 999,
        "courseName": "River Loop",
        "distanceMeter": 140.0,
        "elevationGainMeter": 5.0,
        "elevationLossMeter": 0.0,
        "activityTypePk": 1,
    }
    mock_garmin_client.client.post.side_effect = [parsed, saved]
    mock_garmin_client.client.domain = "garmin.com"

    result = await app_with_courses.call_tool(
        "upload_course",
        {"gpx_path": str(gpx), "activity_type": "running"},
    )

    data = json.loads(_result_text(result))
    assert data["status"] == "success"
    assert data["course_id"] == 999
    assert data["name"] == "River Loop"
    assert data["activity_type_id"] == 1
    assert "course/999" in data["url"]

    # Two-step flow: POST /import then POST /course.
    assert mock_garmin_client.client.post.call_count == 2
    import_call, create_call = mock_garmin_client.client.post.call_args_list
    assert import_call.args[1] == "/course-service/course/import"
    assert create_call.args[1] == "/course-service/course"


# --- delete_course --------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_course_success(app_with_courses, mock_garmin_client):
    """delete_course hits the right endpoint and reports success."""
    result = await app_with_courses.call_tool("delete_course", {"course_id": 555})

    data = json.loads(_result_text(result))
    assert data["status"] == "success"
    assert data["course_id"] == 555
    mock_garmin_client.client.delete.assert_called_once_with(
        "connectapi", "/course-service/course/555"
    )


@pytest.mark.asyncio
async def test_delete_course_error_is_caught(app_with_courses, mock_garmin_client):
    """A client error is surfaced as a clean message, not a traceback."""
    mock_garmin_client.client.delete.side_effect = Exception("nope")

    result = await app_with_courses.call_tool("delete_course", {"course_id": 555})

    assert "Error deleting course" in _result_text(result)


# --- pure helpers ---------------------------------------------------------

def test_build_course_payload_computes_distance_and_defaults():
    """Distances accumulate, missing elevation defaults to 0, bbox is derived."""
    parsed = {
        "geoPoints": [
            {"latitude": 40.0, "longitude": -105.0, "elevation": 1600.0},
            {"latitude": 40.0, "longitude": -105.0, "elevation": None},
        ],
    }

    payload = _build_course_payload(parsed, "X", 1, None)

    assert payload["courseName"] == "X"
    assert payload["activityTypePk"] == 1
    # Identical points -> zero total distance.
    assert payload["distanceMeter"] == 0.0
    # Missing elevation is backfilled to 0.0.
    assert payload["geoPoints"][1]["elevation"] == 0.0
    assert payload["boundingBox"]["lowerLeft"]["latitude"] == 40.0


def test_build_course_payload_rejects_too_few_points():
    """A GPX with fewer than two points is rejected."""
    with pytest.raises(ValueError):
        _build_course_payload({"geoPoints": [{"latitude": 1, "longitude": 2}]}, "X", 1, None)


def test_haversine_one_degree_latitude():
    """One degree of latitude is ~111 km."""
    distance = _haversine(
        {"latitude": 0.0, "longitude": 0.0},
        {"latitude": 1.0, "longitude": 0.0},
    )
    assert 110000 < distance < 112000

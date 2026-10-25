"""
Integration tests for the courses module MCP tools.

Covers get_courses, get_course_details, download_course_gpx, upload_course, and delete_course
using FastMCP integration with a mocked Garmin client. No real Garmin account or network access is used.
"""
import json
import os
import xml.etree.ElementTree as ET

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import courses
from garmin_mcp.courses import _build_course_payload, _haversine, _resolve_gpx_output_path


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


# --- get_course_details ---------------------------------------------------

@pytest.mark.asyncio
async def test_get_course_details_success(app_with_courses, mock_garmin_client):
    """get_course_details returns full course structure with custom waypoints."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 777,
        "courseName": "Bikepack Day 1",
        "distanceInMeters": 45000.0,
        "elevationGainInMeters": 350.0,
        "elevationLossInMeters": 300.0,
        "activityType": {"typeKey": "gravel_cycling"},
        "coursePoints": [
            {
                "name": "Water Fountain",
                "pointType": "WATER",
                "lat": 52.2,
                "lon": 21.0,
                "distance": 12000.0,
            }
        ],
        "geoPoints": [
            {"latitude": 52.1, "longitude": 20.9},
            {"latitude": 52.2, "longitude": 21.0},
        ],
    }

    result = await app_with_courses.call_tool("get_course_details", {"course_id": 777})

    data = json.loads(_result_text(result))
    assert data["course_id"] == 777
    assert data["name"] == "Bikepack Day 1"
    assert data["distance_m"] == 45000.0
    assert data["waypoints_count"] == 1
    assert data["waypoints"][0]["type"] == "WATER"
    assert data["geo_points_count"] == 2
    mock_garmin_client.client.connectapi.assert_called_once_with("/course-service/course/777")


@pytest.mark.asyncio
async def test_get_course_details_preserves_explicit_zero_metrics(
    app_with_courses, mock_garmin_client
):
    """Explicit zero metrics must not fall through to stale legacy fields."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 780,
        "courseName": "Single Point",
        "distanceInMeters": 0.0,
        "distanceMeter": 123.0,
        "elevationGainInMeters": 0.0,
        "elevationGainMeter": 4.0,
        "elevationLossInMeters": 0.0,
        "elevationLossMeter": 5.0,
        "coursePoints": [],
        "geoPoints": [{"latitude": 52.1, "longitude": 20.9}],
    }

    result = await app_with_courses.call_tool("get_course_details", {"course_id": 780})

    data = json.loads(_result_text(result))
    assert data["distance_m"] == 0.0
    assert data["elevation_gain_m"] == 0.0
    assert data["elevation_loss_m"] == 0.0


@pytest.mark.asyncio
async def test_get_course_details_treats_null_course_points_as_empty(
    app_with_courses, mock_garmin_client
):
    """A course with no waypoints still returns its valid geo-point count."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 778,
        "courseName": "Track Only",
        "coursePoints": None,
        "geoPoints": [
            {"latitude": 52.1, "longitude": 20.9},
            {"latitude": 52.2, "longitude": 21.0},
        ],
    }

    result = await app_with_courses.call_tool("get_course_details", {"course_id": 778})

    data = json.loads(_result_text(result))
    assert data["waypoints_count"] == 0
    assert data["waypoints"] == []
    assert data["geo_points_count"] == 2


@pytest.mark.asyncio
async def test_get_course_details_treats_null_geo_points_as_empty(
    app_with_courses, mock_garmin_client
):
    """A course with no route points reports zero geo points cleanly."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 779,
        "courseName": "Metadata Only",
        "coursePoints": None,
        "geoPoints": None,
    }

    result = await app_with_courses.call_tool("get_course_details", {"course_id": 779})

    data = json.loads(_result_text(result))
    assert data["waypoints_count"] == 0
    assert data["geo_points_count"] == 0


@pytest.mark.asyncio
async def test_get_course_details_error_is_caught(app_with_courses, mock_garmin_client):
    """Client error in get_course_details is caught cleanly."""
    mock_garmin_client.client.connectapi.side_effect = Exception("Not found")

    result = await app_with_courses.call_tool("get_course_details", {"course_id": 999})

    assert "Error fetching course details" in _result_text(result)


# --- download_course_gpx ---------------------------------------------------

@pytest.mark.asyncio
async def test_download_course_gpx_expands_small_numbers_to_decimal(
    app_with_courses, mock_garmin_client, tmp_path
):
    """GPX 1.1 lat/lon/elevation are xsd:decimal, which excludes exponents."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseName": "Near the equator and prime meridian",
        "geoPoints": [{"latitude": 0.00005, "longitude": -0.00005, "elevation": 0.000001}],
        "coursePoints": [{"lat": -0.00000001, "lon": 0.00000001}],
    }
    path = tmp_path / "small_numbers.gpx"
    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 888, "output_path": str(path)}
    )
    assert json.loads(_result_text(result))["status"] == "success"
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    root = ET.parse(path).getroot()
    waypoint = root.find("gpx:wpt", ns)
    point = root.find("gpx:trk/gpx:trkseg/gpx:trkpt", ns)
    assert waypoint.attrib == {"lat": "-0.00000001", "lon": "0.00000001"}
    assert point.attrib == {"lat": "0.00005", "lon": "-0.00005"}
    assert point.find("gpx:ele", ns).text == "0.000001"


@pytest.mark.asyncio
async def test_download_course_gpx_success(app_with_courses, mock_garmin_client, tmp_path):
    """download_course_gpx formats a valid GPX 1.1 file and writes to disk."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 888,
        "courseName": "Mountain Pass",
        "geoPoints": [
            {"latitude": 46.1, "longitude": 8.1, "elevation": 1200.0},
            {"latitude": 46.2, "longitude": 8.2, "elevation": 1400.0},
        ],
        "coursePoints": [
            {"name": "Summit Rest", "pointType": "SUMMIT", "lat": 46.2, "lon": 8.2}
        ],
    }

    out_file = str(tmp_path / "mountain_pass.gpx")
    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 888, "output_path": out_file}
    )

    data = json.loads(_result_text(result))
    assert data["status"] == "success"
    assert data["course_id"] == 888
    assert data["waypoints_count"] == 1
    assert data["track_points_count"] == 2
    assert os.path.isfile(out_file)

    with open(out_file, "r", encoding="utf-8") as f:
        content = f.read()

    assert "<gpx version='1.1'" in content
    assert "<name>Mountain Pass</name>" in content
    assert "<wpt lat='46.2' lon='8.2'>" in content
    assert "<name>Summit Rest</name>" in content
    assert "<trkpt lat='46.1' lon='8.1'><ele>1200.0</ele></trkpt>" in content


@pytest.mark.asyncio
async def test_download_course_gpx_escapes_xml_special_characters(
    app_with_courses, mock_garmin_client, tmp_path
):
    """Names containing &, < or > still produce well-formed GPX."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 891,
        "courseName": "Rock & Roll <Loop>",
        "geoPoints": [
            {"latitude": 46.1, "longitude": 8.1, "elevation": 1200.0},
            {"latitude": 46.2, "longitude": 8.2, "elevation": 1400.0},
        ],
        "coursePoints": [
            {"name": "Café & Water", "pointType": "WATER", "lat": 46.2, "lon": 8.2}
        ],
    }
    out_file = tmp_path / "rock_and_roll.gpx"

    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 891, "output_path": str(out_file)}
    )

    data = json.loads(_result_text(result))
    assert data["status"] == "success"
    assert data["name"] == "Rock & Roll <Loop>"

    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    root = ET.parse(out_file).getroot()
    assert root.find("gpx:metadata/gpx:name", ns).text == "Rock & Roll <Loop>"
    assert root.find("gpx:trk/gpx:name", ns).text == "Rock & Roll <Loop>"
    assert root.find("gpx:wpt/gpx:name", ns).text == "Café & Water"
    assert root.find("gpx:wpt/gpx:type", ns).text == "WATER"
    assert len(root.findall("gpx:trk/gpx:trkseg/gpx:trkpt", ns)) == 2


@pytest.mark.asyncio
async def test_download_course_gpx_null_course_name_uses_default(
    app_with_courses, mock_garmin_client, tmp_path
):
    """A null courseName falls back to a default instead of writing 'None'."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 892,
        "courseName": None,
        "geoPoints": [{"latitude": 46.1, "longitude": 8.1, "elevation": None}],
        "coursePoints": [{"name": None, "pointType": None, "lat": 46.1, "lon": 8.1}],
    }
    out_file = tmp_path / "unnamed.gpx"

    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 892, "output_path": str(out_file)}
    )

    data = json.loads(_result_text(result))
    assert data["name"] == "Garmin Course 892"
    content = out_file.read_text(encoding="utf-8")
    assert "None" not in content

    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    root = ET.fromstring(content)
    assert root.find("gpx:metadata/gpx:name", ns).text == "Garmin Course 892"
    assert root.find("gpx:wpt/gpx:name", ns).text == "Waypoint"
    assert root.find("gpx:wpt/gpx:type", ns).text == "GENERIC"


@pytest.mark.asyncio
async def test_download_course_gpx_preserves_track_when_course_points_are_null(
    app_with_courses, mock_garmin_client, tmp_path
):
    """Null waypoints do not prevent exporting the valid track."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 889,
        "courseName": "Track Only",
        "geoPoints": [
            {"latitude": 46.1, "longitude": 8.1, "elevation": 1200.0},
            {"latitude": 46.2, "longitude": 8.2, "elevation": 1400.0},
        ],
        "coursePoints": None,
    }
    out_file = str(tmp_path / "track_only.gpx")

    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 889, "output_path": out_file}
    )

    data = json.loads(_result_text(result))
    assert data["status"] == "success"
    assert data["waypoints_count"] == 0
    assert data["track_points_count"] == 2
    assert "<trkpt lat='46.1' lon='8.1'><ele>1200.0</ele></trkpt>" in (
        open(out_file, encoding="utf-8").read()
    )


@pytest.mark.asyncio
async def test_download_course_gpx_reports_null_geo_points_without_writing(
    app_with_courses, mock_garmin_client, tmp_path
):
    """Null geo points are reported as missing route data, not iterated."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 890,
        "courseName": "Metadata Only",
        "geoPoints": None,
        "coursePoints": None,
    }
    out_file = tmp_path / "metadata_only.gpx"

    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 890, "output_path": str(out_file)}
    )

    assert "Error: course 890 not found or missing geoPoints" in _result_text(result)
    assert not out_file.exists()


@pytest.mark.asyncio
async def test_download_course_gpx_missing_course(app_with_courses, mock_garmin_client):
    """Missing course surfaces a clean error message."""
    mock_garmin_client.client.connectapi.return_value = {}

    result = await app_with_courses.call_tool("download_course_gpx", {"course_id": 404})

    assert "Error: course 404 not found" in _result_text(result)


_TRACK_ONLY_COURSE = {
    "courseId": 893,
    "courseName": "Track Only",
    "geoPoints": [{"latitude": 46.1, "longitude": 8.1, "elevation": 1200.0}],
    "coursePoints": None,
}


@pytest.mark.asyncio
async def test_download_course_gpx_rejects_non_gpx_output_file(
    app_with_courses, mock_garmin_client, tmp_path
):
    """A non-.gpx file path is rejected and the existing file is left untouched."""
    mock_garmin_client.client.connectapi.return_value = dict(_TRACK_ONLY_COURSE)
    target = tmp_path / "x" / ".zshrc"
    target.parent.mkdir()
    target.write_text("export PATH=/usr/bin\n")

    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 893, "output_path": str(target)}
    )

    assert "output_path must be a directory or a .gpx file" in _result_text(result)
    assert target.read_text() == "export PATH=/usr/bin\n"
    mock_garmin_client.client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_download_course_gpx_accepts_gpx_output_file(
    app_with_courses, mock_garmin_client, tmp_path
):
    """A file path ending in .gpx (any case) is written as given."""
    mock_garmin_client.client.connectapi.return_value = dict(_TRACK_ONLY_COURSE)
    target = tmp_path / "Route.GPX"

    result = await app_with_courses.call_tool(
        "download_course_gpx", {"course_id": 893, "output_path": str(target)}
    )

    data = json.loads(_result_text(result))
    assert data["gpx_path"] == str(target)
    assert ET.parse(target).getroot().tag == "{http://www.topografix.com/GPX/1/1}gpx"


@pytest.mark.asyncio
async def test_download_course_gpx_defaults_to_configured_download_dir(
    app_with_courses, mock_garmin_client, tmp_path, monkeypatch
):
    """Without output_path, the set_fit_download_dir directory is used."""
    mock_garmin_client.client.connectapi.return_value = dict(_TRACK_ONLY_COURSE)
    download_dir = tmp_path / "downloads"
    config_path = tmp_path / "fit_config.json"
    config_path.write_text(json.dumps({"download_dir": str(download_dir)}))
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(config_path))
    monkeypatch.delenv("GARMIN_FIT_DOWNLOAD_DIR", raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    result = await app_with_courses.call_tool("download_course_gpx", {"course_id": 893})

    data = json.loads(_result_text(result))
    assert data["gpx_path"] == str(download_dir / "893.gpx")
    assert (download_dir / "893.gpx").is_file()
    assert not (cwd / "courses").exists()


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
    """A missing file path returns an error message before hitting Garmin."""
    result = await app_with_courses.call_tool(
        "upload_course", {"gpx_path": "/tmp/nonexistent-route-12345.gpx"}
    )

    assert "GPX file not found" in _result_text(result)
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_upload_course_rejects_unknown_activity_type(
    app_with_courses, mock_garmin_client, tmp_path
):
    """Unknown activity_type strings are rejected with supported list."""
    gpx_file = tmp_path / "valid.gpx"
    gpx_file.write_text("<gpx></gpx>")

    result = await app_with_courses.call_tool(
        "upload_course",
        {"gpx_path": str(gpx_file), "activity_type": "paragliding"},
    )

    assert "unknown activity_type 'paragliding'" in _result_text(result)
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_upload_course_success(app_with_courses, mock_garmin_client, tmp_path):
    """Valid GPX file triggers step 1 import and step 2 save, returning metadata."""
    gpx_file = tmp_path / "test.gpx"
    gpx_file.write_text("<gpx><trk><trkseg></trkseg></trk></gpx>")

    mock_garmin_client.client.post.side_effect = [
        # Step 1: /course-service/course/import response skeleton
        {
            "courseName": "River Loop",
            "geoPoints": [
                {"latitude": 40.0, "longitude": -105.0, "elevation": 1600.0},
                {"latitude": 40.01, "longitude": -105.01, "elevation": 1610.0},
            ],
        },
        # Step 2: /course-service/course save response
        {
            "courseId": 999,
            "courseName": "River Loop",
            "distanceMeter": 1250.0,
            "elevationGainMeter": 10.0,
            "elevationLossMeter": 0.0,
            "activityTypePk": 1,
        },
    ]

    result = await app_with_courses.call_tool(
        "upload_course",
        {
            "gpx_path": str(gpx_file),
            "course_name": "River Loop",
            "activity_type": "running",
        },
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "activity_type,type_id",
    [
        ("running", 1),
        ("cycling", 2),
        ("hiking", 3),
        ("walking", 9),
        ("mountain_biking", 5),
        ("trail_running", 6),
        ("road_biking", 10),
        ("gravel_cycling", 143),
    ],
)
async def test_upload_course_posts_garmin_activity_type_id(
    app_with_courses, mock_garmin_client, tmp_path, activity_type, type_id
):
    """Each supported activity_type maps to Garmin's real activity typeId."""
    gpx_file = tmp_path / "route.gpx"
    gpx_file.write_text("<gpx><trk><trkseg></trkseg></trk></gpx>")

    mock_garmin_client.client.post.side_effect = [
        {
            "courseName": "Route",
            "geoPoints": [
                {"latitude": 40.0, "longitude": -105.0, "elevation": 1600.0},
                {"latitude": 40.01, "longitude": -105.01, "elevation": 1610.0},
            ],
        },
        {"courseId": 1000, "courseName": "Route", "activityTypePk": type_id},
    ]

    result = await app_with_courses.call_tool(
        "upload_course",
        {"gpx_path": str(gpx_file), "activity_type": activity_type},
    )

    assert json.loads(_result_text(result))["status"] == "success"
    create_call = mock_garmin_client.client.post.call_args_list[1]
    assert create_call.kwargs["json"]["activityTypePk"] == type_id


# --- delete_course --------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_course_requires_confirmation(app_with_courses, mock_garmin_client):
    """delete_course returns a preview and deletes nothing unless confirm=true."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 555,
        "courseName": "River Loop",
        "distanceMeter": 10250.5,
        "activityType": {"typeKey": "running"},
    }

    result = await app_with_courses.call_tool("delete_course", {"course_id": 555})

    data = json.loads(_result_text(result))
    assert data["status"] == "needs_confirmation"
    assert data["course"]["name"] == "River Loop"
    assert data["course"]["distance_m"] == 10250.5
    mock_garmin_client.client.connectapi.assert_called_once_with("/course-service/course/555")
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_course_success(app_with_courses, mock_garmin_client):
    """delete_course with confirm=true hits the right endpoint and reports success."""
    mock_garmin_client.client.connectapi.return_value = {
        "courseId": 555,
        "courseName": "River Loop",
    }

    result = await app_with_courses.call_tool(
        "delete_course", {"course_id": 555, "confirm": True}
    )

    data = json.loads(_result_text(result))
    assert data["status"] == "success"
    assert data["course_id"] == 555
    assert data["course"]["name"] == "River Loop"
    mock_garmin_client.client.delete.assert_called_once_with(
        "connectapi", "/course-service/course/555"
    )


@pytest.mark.asyncio
async def test_delete_course_error_is_caught(app_with_courses, mock_garmin_client):
    """A client error is surfaced as a clean message, not a traceback."""
    mock_garmin_client.client.connectapi.return_value = {"courseId": 555}
    mock_garmin_client.client.delete.side_effect = Exception("nope")

    result = await app_with_courses.call_tool(
        "delete_course", {"course_id": 555, "confirm": True}
    )

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
    """A GPX skeleton without geoPoints raises ValueError."""
    with pytest.raises(ValueError, match="no geoPoints"):
        _build_course_payload({}, "X", 1, None)


def test_resolve_gpx_output_path():
    """Output path resolution precedence and directory handling."""
    # 1. Custom file path
    assert _resolve_gpx_output_path(123, "/tmp/custom.gpx") == "/tmp/custom.gpx"

    # 2. Custom directory path
    assert _resolve_gpx_output_path(123, "/tmp/dir/") == "/tmp/dir/123.gpx"

    # 3. Default path
    resolved_default = _resolve_gpx_output_path(123)
    assert resolved_default.endswith("courses/123.gpx") or "123.gpx" in resolved_default

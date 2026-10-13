"""Unit tests for delete_course preview helper."""

from types import SimpleNamespace

from garmin_mcp.courses import _course_preview


class _FakeConnect:
    def __init__(self, course=None, courses=None, error=None):
        self._course = course
        self._courses = courses
        self._error = error

    def connectapi(self, path):
        if path == "/course-service/course":
            return self._courses
        if self._error:
            raise self._error
        return self._course


def _client(**kwargs):
    return SimpleNamespace(client=_FakeConnect(**kwargs))


def test_preview_from_course_details():
    client = _client(
        course={
            "courseId": 99,
            "courseName": "River Loop",
            "distanceMeter": 10250.5,
            "activityType": {"typeKey": "running"},
        }
    )
    preview = _course_preview(client, 99)
    assert preview["course_id"] == 99
    assert preview["name"] == "River Loop"
    assert preview["activity"] == "running"
    assert preview["distance_m"] == 10250.5


def test_preview_falls_back_to_course_list_on_details_error():
    client = _client(
        error=Exception("403 Forbidden"),
        courses=[
            {"courseId": 98, "courseName": "Other Course"},
            {
                "courseId": 99,
                "courseName": "Gravel Loop",
                "distanceInMeters": 45000.0,
                "activityType": {"typeKey": "gravel_cycling"},
                "createdDateFormatted": "2024-03-01",
            },
        ],
    )
    preview = _course_preview(client, 99)
    assert preview["name"] == "Gravel Loop"
    assert preview["activity"] == "gravel_cycling"
    assert preview["created"] == "2024-03-01"


def test_preview_with_no_course_data_keeps_only_id():
    client = _client(error=Exception("404 Not Found"), courses=[])
    assert _course_preview(client, 99) == {"course_id": 99}

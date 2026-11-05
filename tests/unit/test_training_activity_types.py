"""Activity type labels recover when a lookup fails transiently."""

from unittest.mock import Mock

from garmin_mcp import training


def test_activity_type_mapping_retries_after_transient_failure(monkeypatch):
    client = Mock()
    client.get_activity_types.side_effect = [
        Exception("temporary lookup failure"),
        [{"typeId": 3, "typeKey": "hiking"}],
    ]
    monkeypatch.setattr(training, "garmin_client", client)
    monkeypatch.setattr(training, "_activity_type_cache", None)

    assert training._get_activity_type_mapping() == {}
    assert training._get_activity_type_mapping() == {3: "hiking"}
    assert training._get_activity_type_mapping() == {3: "hiking"}
    assert client.get_activity_types.call_count == 2

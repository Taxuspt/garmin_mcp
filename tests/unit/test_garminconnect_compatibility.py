"""Compatibility checks for behavior supplied by the pinned garminconnect."""

from unittest.mock import patch

import pytest
from garminconnect import Garmin


@pytest.mark.parametrize(
    ("hole_numbers", "expected_params"),
    [
        pytest.param("1,2,3", "hole-numbers=1-2-3", id="commas-normalized"),
        pytest.param("1-2-3", "hole-numbers=1-2-3", id="dashes-preserved"),
        pytest.param(None, None, id="all-holes"),
    ],
)
def test_golf_hole_number_request_contract(hole_numbers, expected_params):
    """Pin 0.3.8's request contract so dependency upgrades require review."""
    client = Garmin()

    with patch.object(
        client,
        "connectapi",
        return_value={"holes": []},
    ) as connectapi:
        result = client.get_golf_shot_data(12345, hole_numbers=hole_numbers)

    assert result == {"holes": []}
    connectapi.assert_called_once()
    (url,), kwargs = connectapi.call_args
    assert url.endswith("/12345/hole")
    assert kwargs["params"] == expected_params

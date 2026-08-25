"""Tests for Desktop Extension user-config handling."""

from unittest.mock import patch

import pytest

from garmin_mcp import (
    _normalize_optional_user_config,
    init_api,
)


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("${user_config.garmin_email}", "garmin_email"),
        ("${user_config.garmin_password}", "garmin_password"),
    ],
)
def test_unresolved_optional_user_config_is_treated_as_unset(value, key):
    assert _normalize_optional_user_config(value, key) is None


def test_real_value_containing_template_syntax_is_preserved():
    value = "secret-${not_a_user_config_placeholder}"

    assert _normalize_optional_user_config(value, "garmin_password") == value


@patch("garmin_mcp.is_interactive_terminal", return_value=False)
@patch("garmin_mcp.GarminSession")
def test_unresolved_credentials_do_not_trigger_login(mock_session_cls, _mock_terminal):
    mock_session_cls.return_value.has_stored_tokens.return_value = False

    result = init_api(
        "${user_config.garmin_email}",
        "${user_config.garmin_password}",
    )

    assert result is None
    mock_session_cls.return_value.warm.assert_not_called()
    # The literal placeholder strings must have been normalized to None
    # before reaching the session -- not passed through as real credentials.
    _, kwargs = mock_session_cls.call_args
    assert kwargs["email"] is None
    assert kwargs["password"] is None

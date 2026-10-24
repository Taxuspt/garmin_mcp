"""init_api / GARMIN_AUTH_MODE wiring for the official Personal API backend.

These tests pin the contract that matters for existing installs: with no key
the SSO path is unchanged, and with a key no SSO request is ever made.
"""

import pytest

import garmin_mcp
from garmin_mcp import personal_api


class FakePersonalGarmin:
    """Just enough of a ``Garmin`` for the startup reporting."""

    class _Inner:
        base_url = "https://personal.connectapi.garmin.com"

    def __init__(self, display_name="denis"):
        self.client = FakePersonalGarmin._Inner()
        self.display_name = display_name


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        personal_api.API_KEY_ENV_VAR,
        personal_api.API_KEY_FILE_ENV_VAR,
        personal_api.BASE_URL_ENV_VAR,
        "GARMIN_AUTH_MODE",
        "GARMIN_EMAIL",
        "GARMIN_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_init_api_returns_the_personal_client(monkeypatch, capsys):
    client = FakePersonalGarmin()
    monkeypatch.setenv(personal_api.API_KEY_ENV_VAR, "KEY")
    monkeypatch.setattr(personal_api, "login_personal", lambda key, **kw: client)
    monkeypatch.setattr(
        garmin_mcp, "Garmin", lambda *a, **k: pytest.fail("SSO login must not run")
    )

    assert garmin_mcp.init_api(None, None) is client
    assert "Personal API" in capsys.readouterr().err


def test_personal_mode_without_key_does_not_fall_back(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_AUTH_MODE", "personal")
    monkeypatch.setenv(personal_api.API_KEY_FILE_ENV_VAR, str(tmp_path / "missing"))
    monkeypatch.setattr(
        garmin_mcp, "Garmin", lambda *a, **k: pytest.fail("SSO login must not run")
    )

    assert garmin_mcp.init_api(None, None) is None


def test_auto_mode_ignores_a_rejected_key_and_returns_none(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(personal_api.API_KEY_ENV_VAR, "BAD")
    monkeypatch.setattr(
        personal_api,
        "login_personal",
        lambda key, **kw: (_ for _ in ()).throw(personal_api.PersonalApiAuthError("rejected")),
    )

    assert garmin_mcp._init_personal_api("auto") is None
    assert "Falling back" in capsys.readouterr().err


def test_auto_mode_actually_continues_to_sso(monkeypatch, capsys, tmp_path):
    """A rejected key in 'auto' must not leave init_api without a client."""

    class FakeSsoGarmin:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args, **kwargs):
            return (None, None)

    monkeypatch.setenv(personal_api.API_KEY_ENV_VAR, "BAD")
    monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "tokens"))
    monkeypatch.setattr(
        personal_api,
        "login_personal",
        lambda key, **kw: (_ for _ in ()).throw(personal_api.PersonalApiAuthError("rejected")),
    )
    monkeypatch.setattr(garmin_mcp, "Garmin", FakeSsoGarmin)

    client = garmin_mcp.init_api(None, None)

    assert isinstance(client, FakeSsoGarmin)
    assert "Falling back" in capsys.readouterr().err


def test_personal_mode_reports_without_falling_back_message(monkeypatch, capsys):
    monkeypatch.setattr(
        personal_api,
        "login_personal",
        lambda key, **kw: (_ for _ in ()).throw(personal_api.PersonalApiAuthError("rejected")),
    )

    assert garmin_mcp._init_personal_api("personal") is None
    assert "Falling back" not in capsys.readouterr().err


def test_sso_mode_never_reads_a_personal_key(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_AUTH_MODE", "sso")
    monkeypatch.setenv(personal_api.API_KEY_ENV_VAR, "KEY")
    monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "no-tokens"))
    monkeypatch.setattr(
        personal_api, "login_personal", lambda *a, **k: pytest.fail("must not run")
    )

    # No saved tokens and no credentials: the SSO path reports and gives up.
    assert garmin_mcp.init_api(None, None, auth_mode="sso") is None


def test_resolve_auth_mode_rejects_unknown(monkeypatch):
    monkeypatch.setenv("GARMIN_AUTH_MODE", "oauth1")
    with pytest.raises(ValueError) as info:
        garmin_mcp._resolve_auth_mode()
    assert "GARMIN_AUTH_MODE" in str(info.value)


def test_resolve_auth_mode_defaults_to_auto(monkeypatch):
    assert garmin_mcp._resolve_auth_mode() == "auto"

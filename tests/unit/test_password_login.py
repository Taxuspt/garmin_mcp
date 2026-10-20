"""Password logins must use the given credentials and load the profile.

These tests run the real garminconnect ``Garmin.login`` logic and fake only
the client's token files and HTTP calls, so they catch the library falling
back to ``$GARMINTOKENS`` when ``login()`` is called without a tokenstore.
"""

import os
from unittest.mock import patch

import pytest
from garminconnect import GarminConnectConnectionError
from garminconnect.client import Client

import garmin_mcp
from garmin_mcp import auth_cli


@pytest.fixture
def fake_garmin(monkeypatch):
    """Fake Garmin token storage and API keyed by token directory.

    A token is the string "<state>:<email>". "valid" and "fresh" tokens
    authenticate; "revoked" tokens load from disk but every API call fails.
    """
    store = {}

    def load(self, path):
        if path not in store:
            raise FileNotFoundError(path)
        self._fake_token = store[path]
        self._tokenstore_path = path

    def login(self, email, password, prompt_mfa=None, return_on_mfa=False):
        self._fake_token = f"fresh:{email}"
        return None, None

    def dump(self, path):
        store[path] = self._fake_token
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "garmin_tokens.json"), "w") as f:
            f.write("{}")

    def connectapi(self, path, **kwargs):
        token = getattr(self, "_fake_token", None)
        if token is None or token.startswith("revoked:"):
            raise GarminConnectConnectionError("API Error 401 - Unauthorized")
        email = token.split(":", 1)[1]
        if "socialProfile" in path:
            return {"displayName": email, "fullName": email}
        return {"userData": {"measurementSystem": "metric"}}

    monkeypatch.setattr(Client, "load", load)
    monkeypatch.setattr(Client, "login", login)
    monkeypatch.setattr(Client, "dump", dump)
    monkeypatch.setattr(Client, "connectapi", connectapi)
    monkeypatch.setattr(Client, "_token_expires_soon", lambda self: False)
    monkeypatch.setattr("garminconnect.time.sleep", lambda _s: None)
    return store


def test_cli_password_login_ignores_garmintokens(fake_garmin, monkeypatch, tmp_path):
    """$GARMINTOKENS pointing at another account must not replace the login."""
    alice_dir = str(tmp_path / "alice")
    bob_dir = str(tmp_path / "bob")
    fake_garmin[alice_dir] = "valid:alice@example.com"
    monkeypatch.setenv("GARMINTOKENS", alice_dir)

    with patch.object(auth_cli, "get_credentials", return_value=("bob@example.com", "secret")):
        result = auth_cli.authenticate(bob_dir, str(tmp_path / "bob_base64"), force_reauth=True)

    assert result is True
    assert fake_garmin[bob_dir] == "fresh:bob@example.com"
    assert fake_garmin[alice_dir] == "valid:alice@example.com"


def test_server_password_login_replaces_revoked_garmintokens(fake_garmin, monkeypatch, tmp_path):
    """Revoked tokens in $GARMINTOKENS must not block the credential fallback."""
    token_dir = str(tmp_path / "tokens")
    fake_garmin[token_dir] = "revoked:bob@example.com"
    monkeypatch.setenv("GARMINTOKENS", token_dir)

    garmin = garmin_mcp.init_api(
        "bob@example.com",
        "secret",
        tokenstore=token_dir,
        tokenstore_base64=str(tmp_path / "tokens_base64"),
    )

    assert garmin is not None
    assert fake_garmin[token_dir] == "fresh:bob@example.com"
    assert garmin.display_name == "bob@example.com"


def test_server_password_login_loads_profile(fake_garmin, monkeypatch, tmp_path):
    """A login without MFA returns a client with the profile and settings loaded."""
    token_dir = str(tmp_path / "tokens")
    monkeypatch.delenv("GARMINTOKENS", raising=False)

    garmin = garmin_mcp.init_api(
        "bob@example.com",
        "secret",
        tokenstore=token_dir,
        tokenstore_base64=str(tmp_path / "tokens_base64"),
    )

    assert garmin is not None
    assert garmin.display_name == "bob@example.com"
    assert garmin.unit_system == "metric"
    assert garmin.client._tokenstore_path == token_dir

"""
OAuth2 Authorization Server Provider for Garmin MCP remote server.

Implements OAuthAuthorizationServerProvider from the MCP SDK with SQLite storage.
Users authenticate directly with their Garmin Connect credentials (email + password),
with support for 2FA via a second web page.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Optional

from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    OAuthToken,
    RefreshToken,
    AccessToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

logger = logging.getLogger(__name__)

# TTL for pending MFA state (seconds)
_MFA_TTL = 300  # 5 minutes


@dataclass
class _PendingMfa:
    """Temporary storage for garth client_state during 2FA flow."""

    client_state: dict[str, Any]
    garmin_email: str
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() - self.created_at > _MFA_TTL


class GarminOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth2 provider backed by SQLite for the Garmin MCP server."""

    def __init__(self, db_path: str, server_url: str, session_manager=None):
        self.db_path = db_path
        self.server_url = server_url.rstrip("/")
        self.session_manager = session_manager
        self._pending_mfa: dict[str, _PendingMfa] = {}
        self._mfa_lock = threading.Lock()
        self._init_db()

    # ─── Database ────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = self._get_conn()
        try:
            # Check if old schema exists (with username/password_hash columns)
            # Migrate users table if old schema (username/password_hash)
            user_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "username" in user_cols:
                conn.executescript("DROP TABLE IF EXISTS users;")

            # Migrate auth_codes table if missing client_state column
            ac_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(auth_codes)").fetchall()
            }
            if ac_cols and "client_state" not in ac_cols:
                conn.execute(
                    "ALTER TABLE auth_codes ADD COLUMN client_state TEXT"
                )

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    garmin_email TEXT UNIQUE NOT NULL,
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                );
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    client_info_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_codes (
                    code TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    user_id TEXT,
                    scopes TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    redirect_uri_provided_explicitly INTEGER NOT NULL DEFAULT 1,
                    client_state TEXT,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_tokens (
                    token TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    user_id TEXT,
                    scopes TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    user_id TEXT,
                    scopes TEXT NOT NULL,
                    expires_at REAL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    # ─── User helpers ─────────────────────────────────────────────────

    def _get_or_create_user(self, garmin_email: str) -> str:
        """Upsert a user by Garmin email. Returns user_id."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM users WHERE garmin_email = ?", (garmin_email,)
            ).fetchone()
            if row:
                return row["id"]

            user_id = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO users (id, garmin_email) VALUES (?, ?)",
                (user_id, garmin_email),
            )
            conn.commit()
            return user_id
        finally:
            conn.close()

    def _complete_auth_flow(self, state: str, user_id: str) -> Response:
        """Shared helper: look up pending auth, create auth code, redirect."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM auth_codes WHERE code = ? AND user_id IS NULL",
                (state,),
            ).fetchone()

            if not row:
                # Check if the state exists but already has a user_id (double submit)
                used = conn.execute(
                    "SELECT code FROM auth_codes WHERE code = ?", (state,)
                ).fetchone()
                if used:
                    logger.warning(
                        "Auth state %s already consumed (user_id set)", state[:8]
                    )
                else:
                    logger.warning("Auth state %s not found in DB", state[:8])
                return HTMLResponse(
                    "<h1>Authorization expired</h1><p>Please try again.</p>",
                    status_code=400,
                )

            if row["expires_at"] < time.time():
                logger.warning(
                    "Auth state %s expired: created at %.0f, "
                    "expired at %.0f, now %.0f (%.0fs late)",
                    state[:8],
                    row["expires_at"] - 900,
                    row["expires_at"],
                    time.time(),
                    time.time() - row["expires_at"],
                )
                return HTMLResponse(
                    "<h1>Authorization expired</h1><p>Please try again.</p>",
                    status_code=400,
                )

            # Generate actual authorization code
            auth_code = secrets.token_urlsafe(32)

            conn.execute(
                """INSERT INTO auth_codes
                   (code, client_id, user_id, scopes, code_challenge, redirect_uri,
                    redirect_uri_provided_explicitly, client_state, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    auth_code,
                    row["client_id"],
                    user_id,
                    row["scopes"],
                    row["code_challenge"],
                    row["redirect_uri"],
                    row["redirect_uri_provided_explicitly"],
                    row["client_state"],
                    time.time() + 300,  # 5 min to exchange
                ),
            )

            # Delete the state placeholder
            conn.execute("DELETE FROM auth_codes WHERE code = ?", (state,))
            conn.commit()

            redirect_uri = row["redirect_uri"]
            client_state = row["client_state"]
        finally:
            conn.close()

        redirect_url = construct_redirect_uri(
            redirect_uri, code=auth_code, state=client_state
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    def _cleanup_expired_mfa(self) -> None:
        """Remove expired pending MFA entries. Must be called under _mfa_lock."""
        expired = [k for k, v in self._pending_mfa.items() if v.is_expired()]
        for k in expired:
            del self._pending_mfa[k]

    # ─── OAuthAuthorizationServerProvider methods ─────────────────────

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT client_info_json FROM oauth_clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if row:
                return OAuthClientInformationFull.model_validate_json(
                    row["client_info_json"]
                )
            return None
        finally:
            conn.close()

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_clients (client_id, client_info_json) VALUES (?, ?)",
                (client_info.client_id, client_info.model_dump_json()),
            )
            conn.commit()
        finally:
            conn.close()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Redirect to login page with state info."""
        state_token = secrets.token_urlsafe(32)
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO auth_codes
                   (code, client_id, user_id, scopes, code_challenge, redirect_uri,
                    redirect_uri_provided_explicitly, client_state, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state_token,
                    client.client_id,
                    None,  # user not yet authenticated
                    ",".join(params.scopes or []),
                    params.code_challenge,
                    str(params.redirect_uri),
                    1 if params.redirect_uri_provided_explicitly else 0,
                    params.state,
                    time.time() + 900,  # 15 min to complete login
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return f"{self.server_url}/login?state={state_token}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT * FROM auth_codes
                   WHERE code = ? AND client_id = ? AND user_id IS NOT NULL""",
                (authorization_code, client.client_id),
            ).fetchone()
            if not row or row["expires_at"] < time.time():
                return None
            return AuthorizationCode(
                code=row["code"],
                client_id=row["client_id"],
                scopes=row["scopes"].split(",") if row["scopes"] else [],
                code_challenge=row["code_challenge"],
                redirect_uri=row["redirect_uri"],
                redirect_uri_provided_explicitly=bool(
                    row["redirect_uri_provided_explicitly"]
                ),
                expires_at=row["expires_at"],
            )
        finally:
            conn.close()

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT user_id FROM auth_codes WHERE code = ?",
                (authorization_code.code,),
            ).fetchone()
            user_id = row["user_id"] if row else None

            conn.execute(
                "DELETE FROM auth_codes WHERE code = ?", (authorization_code.code,)
            )

            access_token_str = secrets.token_urlsafe(48)
            refresh_token_str = secrets.token_urlsafe(48)
            access_expires = time.time() + 3600  # 1 hour
            refresh_expires = time.time() + 86400 * 30  # 30 days

            conn.execute(
                """INSERT INTO access_tokens (token, client_id, user_id, scopes, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    access_token_str,
                    client.client_id,
                    user_id,
                    ",".join(authorization_code.scopes),
                    access_expires,
                ),
            )
            conn.execute(
                """INSERT INTO refresh_tokens (token, client_id, user_id, scopes, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    refresh_token_str,
                    client.client_id,
                    user_id,
                    ",".join(authorization_code.scopes),
                    refresh_expires,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        if user_id and self.session_manager:
            self.session_manager.set_token_user_mapping(access_token_str, user_id)

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh_token_str,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM access_tokens WHERE token = ?", (token,)
            ).fetchone()
            if not row or row["expires_at"] < time.time():
                return None

            if row["user_id"] and self.session_manager:
                self.session_manager.set_token_user_mapping(token, row["user_id"])

            return AccessToken(
                token=row["token"],
                client_id=row["client_id"],
                scopes=row["scopes"].split(",") if row["scopes"] else [],
                expires_at=int(row["expires_at"]),
            )
        finally:
            conn.close()

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM refresh_tokens WHERE token = ? AND client_id = ?",
                (refresh_token, client.client_id),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] and row["expires_at"] < time.time():
                return None
            return RefreshToken(
                token=row["token"],
                client_id=row["client_id"],
                scopes=row["scopes"].split(",") if row["scopes"] else [],
                expires_at=int(row["expires_at"]) if row["expires_at"] else None,
            )
        finally:
            conn.close()

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT user_id FROM refresh_tokens WHERE token = ?",
                (refresh_token.token,),
            ).fetchone()
            user_id = row["user_id"] if row else None

            conn.execute(
                "DELETE FROM refresh_tokens WHERE token = ?", (refresh_token.token,)
            )

            new_access = secrets.token_urlsafe(48)
            new_refresh = secrets.token_urlsafe(48)
            access_expires = time.time() + 3600
            refresh_expires = time.time() + 86400 * 30

            use_scopes = scopes or refresh_token.scopes
            scopes_str = ",".join(use_scopes)

            conn.execute(
                """INSERT INTO access_tokens (token, client_id, user_id, scopes, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (new_access, client.client_id, user_id, scopes_str, access_expires),
            )
            conn.execute(
                """INSERT INTO refresh_tokens (token, client_id, user_id, scopes, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (new_refresh, client.client_id, user_id, scopes_str, refresh_expires),
            )
            conn.commit()
        finally:
            conn.close()

        if user_id and self.session_manager:
            self.session_manager.set_token_user_mapping(new_access, user_id)

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=new_refresh,
            scope=" ".join(use_scopes),
        )

    async def revoke_token(
        self, token: AccessToken | RefreshToken
    ) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM access_tokens WHERE token = ?", (token.token,))
            conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (token.token,))
            conn.commit()
        finally:
            conn.close()

    # ─── Login pages ─────────────────────────────────────────────────

    # Garmin delta logo as inline SVG
    _GARMIN_LOGO_SVG = (
        '<svg viewBox="0 0 40 40" width="48" height="48" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M20 0 L40 20 L20 40 L0 20 Z" fill="#1a8fc4"/>'
        '<path d="M20 6 L34 20 L20 34 L6 20 Z" fill="white"/>'
        '<path d="M20 12 L28 20 L20 28 L12 20 Z" fill="#1a8fc4"/>'
        "</svg>"
    )

    _PAGE_STYLE = """
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               display: flex; flex-direction: column; justify-content: center; align-items: center;
               min-height: 100vh; margin: 0; background: #f7f8fa; color: #2c3e50; }
        .card { background: white; padding: 2.5rem 2rem 2rem; border-radius: 12px;
                box-shadow: 0 4px 24px rgba(0,0,0,0.08); width: 100%; max-width: 420px; }
        .logo { text-align: center; margin-bottom: 0.5rem; }
        h1 { margin: 0 0 0.25rem; font-size: 1.35rem; text-align: center; color: #1a1a2e; }
        .subtitle { text-align: center; color: #6b7280; font-size: 0.9rem; margin-bottom: 1.5rem; }
        label { display: block; margin-bottom: 0.3rem; font-weight: 600; font-size: 0.85rem;
                color: #374151; }
        input[type="email"], input[type="password"], input[type="text"] {
            width: 100%; padding: 0.65rem 0.75rem; margin-bottom: 1rem;
            border: 1.5px solid #d1d5db; border-radius: 6px; font-size: 0.95rem;
            transition: border-color 0.2s; outline: none; }
        input:focus { border-color: #1a8fc4; box-shadow: 0 0 0 3px rgba(26,143,196,0.12); }
        button { width: 100%; padding: 0.75rem; background: #1a8fc4; color: white;
                 border: none; border-radius: 6px; font-size: 1rem; font-weight: 600;
                 cursor: pointer; transition: background 0.2s; }
        button:hover { background: #157aab; }
        button:active { background: #12688f; }
        .error { color: #991b1b; margin-bottom: 1rem; padding: 0.75rem 1rem;
                 background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px;
                 font-size: 0.9rem; }
        .info { color: #1e40af; margin-bottom: 1rem; padding: 0.75rem 1rem;
                background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 6px;
                text-align: center; font-size: 0.9rem; }
        .privacy { margin-top: 1.25rem; padding-top: 1rem; border-top: 1px solid #e5e7eb;
                   text-align: center; font-size: 0.8rem; color: #6b7280; line-height: 1.5; }
        .privacy svg { vertical-align: middle; margin-right: 0.25rem; }
        .step { display: inline-block; background: #e0f2fe; color: #0369a1; font-size: 0.75rem;
                font-weight: 700; padding: 0.2rem 0.6rem; border-radius: 10px;
                margin-bottom: 0.75rem; }
    """

    _LOCK_ICON = (
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>'
        '<path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>'
    )

    async def get_login_page(self, state: str, error: str = "") -> Response:
        """Render the Garmin Connect login form."""
        error_html = f'<div class="error">{error}</div>' if error else ""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>Garmin MCP - Sign In</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta charset="utf-8">
    <style>{self._PAGE_STYLE}</style>
</head>
<body>
    <div class="card">
        <div class="logo">{self._GARMIN_LOGO_SVG}</div>
        <h1>Connect to Garmin</h1>
        <p class="subtitle">Sign in with your Garmin Connect account to grant access to your fitness data.</p>
        {error_html}
        <form method="POST" action="/login/callback">
            <input type="hidden" name="state" value="{state}">
            <label for="email">Garmin Connect Email</label>
            <input type="email" id="email" name="email" required autofocus
                   placeholder="you@example.com">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required
                   placeholder="Your Garmin password">
            <button type="submit">Sign In with Garmin</button>
        </form>
        <div class="privacy">
            {self._LOCK_ICON} Your credentials are used only to authenticate with Garmin
            and are <strong>never stored</strong> on this server. Only secure session tokens are kept.
        </div>
    </div>
</body>
</html>"""
        return HTMLResponse(html)

    async def handle_login_callback(self, request: Request) -> Response:
        """Handle Garmin Connect login form submission."""
        form = await request.form()
        state = str(form.get("state", ""))
        email = str(form.get("email", ""))
        password = str(form.get("password", ""))

        if not state or not email or not password:
            return await self.get_login_page(state, "All fields are required.")

        try:
            from garth import sso as garth_sso

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                partial(garth_sso.login, email, password, return_on_mfa=True),
            )
        except Exception as e:
            logger.warning("Garmin login failed for %s: %s", email, e)
            return await self.get_login_page(
                state, "Invalid email or password."
            )

        # Check if MFA is required
        if isinstance(result, tuple) and len(result) == 2 and result[0] == "needs_mfa":
            client_state = result[1]
            with self._mfa_lock:
                self._cleanup_expired_mfa()
                self._pending_mfa[state] = _PendingMfa(
                    client_state=client_state,
                    garmin_email=email,
                )
            return RedirectResponse(
                url=f"{self.server_url}/login/mfa?state={state}",
                status_code=302,
            )

        # Login succeeded without MFA
        oauth1_token, oauth2_token = result
        user_id = self._get_or_create_user(email)

        if self.session_manager:
            self.session_manager.create_session_from_garth_tokens(
                user_id, oauth1_token, oauth2_token
            )

        return self._complete_auth_flow(state, user_id)

    async def get_mfa_page(self, state: str, error: str = "") -> Response:
        """Render the 2FA verification form."""
        # Verify the state is valid
        with self._mfa_lock:
            pending = self._pending_mfa.get(state)
            if not pending or pending.is_expired():
                return HTMLResponse(
                    "<h1>Session expired</h1><p>Please start the login process again.</p>",
                    status_code=400,
                )

        error_html = f'<div class="error">{error}</div>' if error else ""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>Garmin MCP - Verification</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta charset="utf-8">
    <style>{self._PAGE_STYLE}</style>
</head>
<body>
    <div class="card">
        <div class="logo">{self._GARMIN_LOGO_SVG}</div>
        <h1>Verify Your Identity</h1>
        <p class="subtitle">One more step to secure your account.</p>
        <div class="info">
            Garmin has sent a verification code to your email or phone.
            Check your inbox and enter the code below.
        </div>
        {error_html}
        <form method="POST" action="/login/mfa/callback">
            <input type="hidden" name="state" value="{state}">
            <label for="mfa_code">Verification Code</label>
            <input type="text" id="mfa_code" name="mfa_code"
                   inputmode="numeric" pattern="[0-9]*" maxlength="7"
                   autocomplete="one-time-code" required autofocus
                   placeholder="Enter 6-digit code"
                   style="text-align:center; font-size:1.5rem; letter-spacing:0.3rem; padding:0.75rem;">
            <button type="submit">Verify &amp; Connect</button>
        </form>
        <div class="privacy">
            {self._LOCK_ICON} This code is sent directly by Garmin.
            We <strong>never</strong> have access to it after verification.
        </div>
    </div>
</body>
</html>"""
        return HTMLResponse(html)

    async def handle_mfa_callback(self, request: Request) -> Response:
        """Handle 2FA verification form submission."""
        form = await request.form()
        state = str(form.get("state", ""))
        mfa_code = str(form.get("mfa_code", "")).strip()

        if not state or not mfa_code:
            return await self.get_mfa_page(state, "Verification code is required.")

        # Pop pending MFA (single use)
        with self._mfa_lock:
            pending = self._pending_mfa.pop(state, None)

        if not pending or pending.is_expired():
            return HTMLResponse(
                "<h1>Session expired</h1><p>Please start the login process again.</p>",
                status_code=400,
            )

        try:
            from garth import sso as garth_sso

            loop = asyncio.get_running_loop()
            oauth1_token, oauth2_token = await loop.run_in_executor(
                None,
                partial(garth_sso.resume_login, pending.client_state, mfa_code),
            )
        except Exception as e:
            logger.warning("MFA verification failed: %s", e)
            # Put the state back so the user can retry
            with self._mfa_lock:
                self._pending_mfa[state] = pending
            return await self.get_mfa_page(state, "Invalid verification code. Please try again.")

        user_id = self._get_or_create_user(pending.garmin_email)

        if self.session_manager:
            self.session_manager.create_session_from_garth_tokens(
                user_id, oauth1_token, oauth2_token
            )

        return self._complete_auth_flow(state, user_id)

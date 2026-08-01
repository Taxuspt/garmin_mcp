"""
Self-serve signup: lets a new person log into their own Garmin account
(email + password, with MFA if needed) and receive their own personal
auth token to use with this server — without you doing it for them.
"""

import io
import json
import secrets
import sys
import tempfile
import time

from garminconnect import Garmin

_pending_logins: dict[str, dict] = {}
_PENDING_TTL_SECONDS = 300  # give someone 5 minutes to enter their MFA code


def _cleanup_pending():
    now = time.time()
    expired = [k for k, v in _pending_logins.items() if now - v["created"] > _PENDING_TTL_SECONDS]
    for k in expired:
        del _pending_logins[k]


def _finish_signup(garmin):
    from garmin_mcp import user_store
    from starlette.responses import JSONResponse

    tmp_dir = tempfile.mkdtemp()
    garmin.client.dump(tmp_dir)
    with open(f"{tmp_dir}/garmin_tokens.json") as f:
        token_json = f.read()

    user_token = secrets.token_urlsafe(32)
    user_store.save_user_tokens(user_token, token_json)
    return JSONResponse({"status": "ok", "token": user_token})


def register_signup_routes(fastmcp, is_cn: bool):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @fastmcp.custom_route("/signup/start", methods=["POST"])
    async def signup_start(request: Request):
        data = await request.json()
        email = data.get("email")
        password = data.get("password")
        if not email or not password:
            return JSONResponse({"error": "email and password required"}, status_code=400)

        _cleanup_pending()

        garmin = Garmin(email=email, password=password, is_cn=is_cn, return_on_mfa=True)
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            result1, result2 = garmin.login()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        finally:
            sys.stdout = old_stdout

        if result1 == "needs_mfa":
            signup_id = secrets.token_urlsafe(16)
            _pending_logins[signup_id] = {
                "garmin": garmin,
                "state": result2,
                "created": time.time(),
            }
            return JSONResponse({"status": "mfa_required", "signup_id": signup_id})

        return _finish_signup(garmin)

    @fastmcp.custom_route("/signup/verify", methods=["POST"])
    async def signup_verify(request: Request):
        data = await request.json()
        signup_id = data.get("signup_id")
        mfa_code = data.get("mfa_code")

        pending = _pending_logins.get(signup_id)
        if not pending:
            return JSONResponse({"error": "signup session not found or expired"}, status_code=400)

        garmin = pending["garmin"]
        try:
            garmin.resume_login(pending["state"], mfa_code)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        del _pending_logins[signup_id]
        return _finish_signup(garmin)

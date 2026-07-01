"""Compatibility fixes for Garmin Connect China authentication.

The garminconnect 0.3.2 client is domain-aware for most China endpoints, but
the DI OAuth token exchange still uses a global module constant. It also tries
the mobile login flow first, whose service URL is not domain-aware. Until the
upstream client handles this natively, patch the client class at runtime.
"""

from __future__ import annotations


def apply_garmin_cn_auth_patch() -> None:
    """Patch garminconnect's client for Garmin Connect China auth flows."""

    from garminconnect import client as client_mod

    client_cls = client_mod.Client
    if getattr(client_cls, "_garmin_mcp_cn_auth_patch_applied", False):
        return

    original_http_post = client_cls._http_post
    original_login = client_cls.login

    def patched_http_post(self, url, **kwargs):
        if (
            getattr(self, "domain", None) == "garmin.cn"
            and url == client_mod.DI_TOKEN_URL
        ):
            url = "https://diauth.garmin.cn/di-oauth2-service/oauth/token"
        return original_http_post(self, url, **kwargs)

    def patched_login(self, email, password, prompt_mfa=None, return_on_mfa=False):
        if getattr(self, "domain", None) != "garmin.cn":
            return original_login(self, email, password, prompt_mfa, return_on_mfa)

        strategies = [
            ("portal+cffi", lambda: self._portal_web_login_cffi(email, password)),
            (
                "portal+requests",
                lambda: self._portal_web_login_requests(email, password),
            ),
            ("mobile+cffi", lambda: self._mobile_login_cffi(email, password)),
            ("mobile+requests", lambda: self._mobile_login_requests(email, password)),
            ("widget+cffi", lambda: self._widget_web_login(email, password)),
        ]

        last_err = None
        rate_limited_count = 0

        for name, run in strategies:
            try:
                client_mod._LOGGER.debug("Trying login strategy: %s", name)
                run()
                return None, None
            except client_mod.GarminConnectAuthenticationError:
                raise
            except client_mod._MFARequired:
                if return_on_mfa:
                    return "needs_mfa", None
                if prompt_mfa:
                    mfa_code = prompt_mfa()
                    self._complete_mfa(mfa_code)
                    return None, None
                raise client_mod.GarminConnectAuthenticationError(
                    "MFA Required but no prompt_mfa mechanism supplied"
                )
            except client_mod.GarminConnectTooManyRequestsError as exc:
                client_mod._LOGGER.warning("%s returned 429: %s", name, exc)
                rate_limited_count += 1
                last_err = exc
                continue
            except Exception as exc:
                client_mod._LOGGER.warning("%s failed: %s", name, exc)
                last_err = exc
                continue

        if rate_limited_count == len(strategies):
            raise client_mod.GarminConnectTooManyRequestsError(
                "All login strategies rate limited (429). "
                "Try again later or check your IP/network."
            )
        raise client_mod.GarminConnectConnectionError(
            f"All login strategies exhausted: {last_err}"
        )

    client_cls._http_post = patched_http_post
    client_cls.login = patched_login
    client_cls._garmin_mcp_cn_auth_patch_applied = True

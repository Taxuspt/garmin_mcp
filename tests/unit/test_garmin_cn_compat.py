"""Regression tests for Garmin Connect China authentication compatibility."""

import json

from garminconnect import client as garmin_client

from garmin_mcp.garmin_cn_compat import configure_garmin_region


def test_configures_china_mobile_service_urls():
    configure_garmin_region(True)

    assert garmin_client.IOS_SERVICE_URL == (
        "https://mobile.integration.garmin.cn/gcm/ios"
    )
    assert garmin_client.MOBILE_SSO_SERVICE_URL == (
        "https://mobile.integration.garmin.cn/gcm/android"
    )


def test_restores_international_mobile_service_urls():
    configure_garmin_region(True)
    configure_garmin_region(False)

    assert garmin_client.IOS_SERVICE_URL == (
        "https://mobile.integration.garmin.com/gcm/ios"
    )
    assert garmin_client.MOBILE_SSO_SERVICE_URL == (
        "https://mobile.integration.garmin.com/gcm/android"
    )


def test_jwt_fallback_state_round_trips():
    configure_garmin_region(True)
    source = garmin_client.Client(domain="garmin.cn")
    source.jwt_web = "jwt-value"
    source.csrf_token = "csrf-value"
    source.cs.cookies.set("JWT_WEB", "jwt-value")
    source.cs.cookies.set("CASTGC", "ticket-cookie")

    serialized = source.dumps()
    data = json.loads(serialized)
    assert data["jwt_web"] == "jwt-value"
    assert data["cookies"]["CASTGC"] == "ticket-cookie"

    restored = garmin_client.Client(domain="garmin.cn")
    restored.loads(serialized)
    assert restored.jwt_web == "jwt-value"
    assert restored.csrf_token == "csrf-value"
    assert restored.cs.cookies.get("CASTGC") == "ticket-cookie"


def test_existing_di_token_store_still_loads():
    configure_garmin_region(False)
    restored = garmin_client.Client()
    restored.loads(
        json.dumps(
            {
                "di_token": "di-value",
                "di_refresh_token": "refresh-value",
                "di_client_id": "client-id",
            }
        )
    )

    assert restored.di_token == "di-value"
    assert restored.di_refresh_token == "refresh-value"
    assert restored.di_client_id == "client-id"

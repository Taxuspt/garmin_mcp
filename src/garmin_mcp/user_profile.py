"""
User Profile functions for Garmin Connect MCP Server
"""
import json
import datetime
import re
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None

_HEART_RATE_ZONES_URL = "/biometric-service/heartRateZones"
_HEART_RATE_ZONE_METHODS = {
    "HR_MAX": "HR_MAX",
    "MAX_HR": "HR_MAX",
    "MAXHR": "HR_MAX",
    "%MAX_HR": "HR_MAX",
    "HR_RESERVE": "HR_RESERVE",
    "HRR": "HR_RESERVE",
    "KARVONEN": "HR_RESERVE",
    "%HRR": "HR_RESERVE",
    "LACTATE_THRESHOLD": "LACTATE_THRESHOLD",
    "LTHR": "LACTATE_THRESHOLD",
    "%LTHR": "LACTATE_THRESHOLD",
    # Garmin does not persist a distinct custom-method value. The setter uses
    # this local sentinel, sends HR_MAX, and relies on the explicit BPM floors.
    "CUSTOM": "CUSTOM_BPM",
    "CUSTOM_BPM": "CUSTOM_BPM",
    "BPM": "CUSTOM_BPM",
}

_HEART_RATE_READBACK_FIELDS = (
    "maxHeartRateUsed",
    "restingHeartRateUsed",
    "restingHrAutoUpdateUsed",
    "lactateThresholdHeartRateUsed",
    "trainingMethod",
    "zone1Floor",
    "zone2Floor",
    "zone3Floor",
    "zone4Floor",
    "zone5Floor",
)


class HeartRateZoneReadbackMismatch(RuntimeError):
    """Garmin returned a zone profile that differs from the committed payload."""

    def __init__(
        self,
        *,
        target: Dict[str, Any],
        confirmed: Dict[str, Any],
        mismatches: Dict[str, Dict[str, Any]],
    ) -> None:
        fields = ", ".join(sorted(mismatches))
        super().__init__(
            "Garmin heart-rate-zone read-back did not match the committed "
            f"payload for: {fields}"
        )
        self.target = target
        self.confirmed = confirmed
        self.mismatches = mismatches


def _normalize_hr_zone_sport(sport: str) -> str:
    """Convert a caller-friendly sport name to Garmin's uppercase sport key."""
    normalized = sport.strip().upper().replace("-", "_").replace(" ", "_")
    if normalized == "GENERIC":
        normalized = "DEFAULT"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized):
        raise ValueError("sport must be a non-empty Garmin sport key such as DEFAULT, RUNNING, or CYCLING")
    return normalized


def _normalize_hr_zone_method(method: str) -> str:
    """Convert supported calculation-method aliases to Garmin's API value."""
    normalized = method.strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return _HEART_RATE_ZONE_METHODS[normalized]
    except KeyError as exc:
        raise ValueError(
            "calculation_method must be one of max_hr, hrr/karvonen, lthr, or custom_bpm"
        ) from exc


def _validate_heart_rate_zone_config(config: Dict[str, Any]) -> None:
    """Validate the merged configuration before sending an account-level write."""
    numeric_fields = {
        "maxHeartRateUsed": "max_hr",
        "restingHeartRateUsed": "resting_hr",
        "lactateThresholdHeartRateUsed": "lactate_threshold_hr",
    }
    for api_name, public_name in numeric_fields.items():
        value = config.get(api_name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= 300
        ):
            raise ValueError(f"{public_name} must be an integer BPM value from 1 to 300")

    max_hr = config.get("maxHeartRateUsed")
    if (
        not isinstance(max_hr, int)
        or isinstance(max_hr, bool)
        or not 0 < max_hr <= 300
    ):
        raise ValueError("the current or supplied max_hr must be an integer BPM value from 1 to 300")

    resting_hr = config.get("restingHeartRateUsed")
    if resting_hr is not None and resting_hr >= max_hr:
        raise ValueError("resting_hr must be lower than max_hr")

    lactate_threshold_hr = config.get("lactateThresholdHeartRateUsed")
    if lactate_threshold_hr is not None and lactate_threshold_hr > max_hr:
        raise ValueError("lactate_threshold_hr must not exceed max_hr")

    boundaries = [config.get(f"zone{zone}Floor") for zone in range(1, 6)]
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= 300
        for value in boundaries
    ):
        raise ValueError("all five zone boundaries must be integer BPM values from 1 to 300")
    if any(lower >= upper for lower, upper in zip(boundaries, boundaries[1:])):
        raise ValueError("zone boundaries must be strictly monotonically increasing")
    if boundaries[-1] > max_hr:
        raise ValueError("zone boundaries must not exceed max_hr")


def _get_heart_rate_zone_configs() -> List[Dict[str, Any]]:
    """Read the saved per-sport zone configuration from Garmin Connect."""
    zones = garmin_client.connectapi(_HEART_RATE_ZONES_URL)
    if not isinstance(zones, list):
        raise ValueError("Garmin returned an unexpected heart-rate-zone response")
    return zones


def prepare_heart_rate_zone_update(
    *,
    sport: str,
    max_hr: Optional[int] = None,
    resting_hr: Optional[int] = None,
    lactate_threshold_hr: Optional[int] = None,
    calculation_method: Optional[str] = None,
    zone_boundaries: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Build and validate a read-modify-write payload without mutating Garmin."""
    supplied = (
        max_hr,
        resting_hr,
        lactate_threshold_hr,
        calculation_method,
        zone_boundaries,
    )
    if all(value is None for value in supplied):
        raise ValueError(
            "No fields to update — supply at least one of max_hr, resting_hr, "
            "lactate_threshold_hr, calculation_method, or zone_boundaries."
        )

    sport_key = _normalize_hr_zone_sport(sport)
    zones = _get_heart_rate_zone_configs()
    saved = next((zone for zone in zones if zone.get("sport") == sport_key), None)
    inherited_from = None
    if saved is None:
        saved = next((zone for zone in zones if zone.get("sport") == "DEFAULT"), None)
        if saved is None:
            raise ValueError(
                f"Could not read current heart-rate zones for {sport_key}, and no "
                "DEFAULT profile is available to inherit — cannot apply update."
            )
        inherited_from = "DEFAULT"

    current = dict(saved)
    payload = dict(saved)
    payload["sport"] = sport_key
    if max_hr is not None:
        payload["maxHeartRateUsed"] = max_hr
    if resting_hr is not None:
        payload["restingHeartRateUsed"] = resting_hr
        payload["restingHrAutoUpdateUsed"] = False
    if lactate_threshold_hr is not None:
        payload["lactateThresholdHeartRateUsed"] = lactate_threshold_hr

    normalized_method = None
    if calculation_method is not None:
        normalized_method = _normalize_hr_zone_method(calculation_method)
        payload["trainingMethod"] = (
            "HR_MAX" if normalized_method == "CUSTOM_BPM" else normalized_method
        )
    if zone_boundaries is not None:
        if len(zone_boundaries) != 5:
            raise ValueError("zone_boundaries must contain exactly five BPM floors")
        for zone, floor in enumerate(zone_boundaries, start=1):
            payload[f"zone{zone}Floor"] = floor

    if zone_boundaries is not None and normalized_method != "CUSTOM_BPM":
        raise ValueError("manual zone_boundaries require calculation_method='custom_bpm'")
    if normalized_method == "CUSTOM_BPM" and zone_boundaries is None:
        raise ValueError("calculation_method='custom_bpm' requires all five zone_boundaries")

    payload["changeState"] = "CHANGED"
    _validate_heart_rate_zone_config(payload)
    return {
        "sport": sport_key,
        "current": current,
        "payload": payload,
        "inherited_from": inherited_from,
    }


def apply_heart_rate_zone_update(prepared: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a payload created by :func:`prepare_heart_rate_zone_update` and read it back."""
    sport_key = prepared.get("sport")
    payload = prepared.get("payload")
    if not isinstance(sport_key, str) or not isinstance(payload, dict):
        raise ValueError("prepared update must contain sport and payload")
    if payload.get("sport") != sport_key:
        raise ValueError("prepared payload sport does not match the requested sport")
    _validate_heart_rate_zone_config(payload)
    garmin_client.client.request(
        "PUT",
        "connectapi",
        _HEART_RATE_ZONES_URL,
        json=[payload],
        api=True,
    )
    confirmed_zones = _get_heart_rate_zone_configs()
    confirmed = next((zone for zone in confirmed_zones if zone.get("sport") == sport_key), None)
    if confirmed is None:
        raise RuntimeError(
            f"Garmin accepted the update request, but no {sport_key} heart-rate "
            "zone profile was present on read-back."
        )
    mismatches = {
        field: {"target": payload.get(field), "confirmed": confirmed.get(field)}
        for field in _HEART_RATE_READBACK_FIELDS
        if payload.get(field) != confirmed.get(field)
    }
    if mismatches:
        raise HeartRateZoneReadbackMismatch(
            target=payload,
            confirmed=confirmed,
            mismatches=mismatches,
        )
    return confirmed


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all user profile tools with the MCP server app"""
    
    @app.tool()
    async def get_full_name() -> str:
        """Get user's full name from profile"""
        try:
            full_name = garmin_client.get_full_name()
            return json.dumps({"full_name": full_name}, indent=2)
        except Exception as e:
            return f"Error retrieving user's full name: {str(e)}"

    @app.tool()
    async def get_unit_system() -> str:
        """Get user's preferred unit system from profile"""
        try:
            unit_system = garmin_client.get_unit_system()
            return json.dumps({"unit_system": unit_system}, indent=2)
        except Exception as e:
            return f"Error retrieving unit system: {str(e)}"
    
    @app.tool()
    async def get_user_profile() -> str:
        """Get user profile information"""
        try:
            profile = garmin_client.get_user_profile()
            if not profile:
                return "No user profile information found."
            return json.dumps(profile, indent=2)
        except Exception as e:
            return f"Error retrieving user profile: {str(e)}"

    @app.tool()
    async def get_userprofile_settings() -> str:
        """Get user profile settings"""
        try:
            settings = garmin_client.get_userprofile_settings()
            if not settings:
                return "No user profile settings found."
            return json.dumps(settings, indent=2)
        except Exception as e:
            return f"Error retrieving user profile settings: {str(e)}"

    @app.tool()
    async def get_heart_rate_zones(sport: Optional[str] = None) -> str:
        """Get the user's saved heart-rate training-zone configuration.

        Garmin stores a generic DEFAULT profile plus optional sport-specific
        overrides such as RUNNING and CYCLING. With no sport, this returns every
        saved profile; pass a sport key to return just that profile.

        Args:
            sport: Optional Garmin sport key (for example default, running, or
                   cycling). "generic" is accepted as an alias for DEFAULT.
        """
        try:
            zones = _get_heart_rate_zone_configs()
            if sport is None:
                if not zones:
                    return "No configured heart-rate zones found."
                return json.dumps(zones, indent=2)

            sport_key = _normalize_hr_zone_sport(sport)
            configured = next((zone for zone in zones if zone.get("sport") == sport_key), None)
            if configured is None:
                return f"No configured heart-rate zones found for sport {sport_key}."
            return json.dumps(configured, indent=2)
        except Exception as e:
            return f"Error retrieving heart-rate zones: {str(e)}"

    @app.tool()
    async def set_heart_rate_zones(
        sport: str,
        max_hr: Optional[int] = None,
        resting_hr: Optional[int] = None,
        lactate_threshold_hr: Optional[int] = None,
        calculation_method: Optional[str] = None,
        zone_boundaries: Optional[List[int]] = None,
        dry_run: bool = False,
    ) -> str:
        """Set an account-level heart-rate training-zone configuration for one sport.

        WARNING: This mutates the user's Garmin Connect account configuration.
        Garmin stores zones per sport, so DEFAULT (the generic fallback),
        RUNNING, CYCLING, and other sport profiles are independent. Only the
        requested sport is written; omitted values are read from that sport's
        current configuration and preserved.

        This change affects future activity recording and zone-based training.
        It does NOT retroactively re-slice already-recorded activities: their
        heart-rate zone boundaries were baked in when those activities were
        recorded.

        The calculation method may be max_hr (% maximum heart rate), hrr or
        karvonen (% heart-rate reserve), lthr (% lactate-threshold heart rate),
        or custom_bpm. Manual zone_boundaries are only accepted with the
        custom_bpm method. Garmin's API does not persist a separate CUSTOM enum:
        direct-BPM zones are sent and read back with trainingMethod=HR_MAX while
        the explicit zone floors remain authoritative.

        The tool performs a read-modify-write and then re-fetches the requested
        sport. Its result is the configuration Garmin actually saved, not the
        request payload.

        Args:
            sport: Garmin sport key, e.g. default/generic, running, or cycling.
            max_hr: Maximum heart rate in BPM.
            resting_hr: Resting heart rate in BPM. Supplying it disables Garmin's
                        resting-HR auto-update for this zone profile.
            lactate_threshold_hr: Lactate-threshold heart rate (LTHR) in BPM.
            calculation_method: max_hr, hrr/karvonen, lthr, or custom_bpm.
            zone_boundaries: Five strictly increasing BPM floors [Z1, Z2, Z3,
                             Z4, Z5]. Every floor must be at most max_hr.
            dry_run: When true, validate and return current/target payloads without
                     writing Garmin. Defaults to false for backward compatibility.
        """
        supplied = (
            max_hr,
            resting_hr,
            lactate_threshold_hr,
            calculation_method,
            zone_boundaries,
        )
        if all(value is None for value in supplied):
            return (
                "No fields to update — supply at least one of max_hr, resting_hr, "
                "lactate_threshold_hr, calculation_method, or zone_boundaries."
            )

        try:
            prepared = prepare_heart_rate_zone_update(
                sport=sport,
                max_hr=max_hr,
                resting_hr=resting_hr,
                lactate_threshold_hr=lactate_threshold_hr,
                calculation_method=calculation_method,
                zone_boundaries=zone_boundaries,
            )
            if dry_run:
                return json.dumps(
                    {
                        "dry_run": True,
                        "sport": prepared["sport"],
                        "current": prepared["current"],
                        "payload": [prepared["payload"]],
                        "write_performed": False,
                    },
                    indent=2,
                )
            return json.dumps(apply_heart_rate_zone_update(prepared), indent=2)
        except ValueError as e:
            return f"Invalid heart-rate zone settings: {str(e)}"
        except Exception as e:
            return f"Error updating heart-rate zones: {str(e)}"

    return app

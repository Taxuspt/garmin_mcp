"""
Activity analysis via FIT file parsing for Garmin Connect MCP Server

Exposes data not available through the REST API:
- DI2 / electronic shifting events (gear combinations, cadence at shift, shift quality, terrain)
- Advanced cycling dynamics (platform center offset, power phase, left/right balance per record)
- Full per-second time series (power, cadence, HR, speed, altitude, GPS)
- Power Duration Curve (best mean maximal power at key durations)
- Climb detection with VAM, grade analysis, W/kg
- HR drift / cardiac drift (aerobic decoupling)
- Temperature correlation with HR and power
- Variability Index per session and lap
"""
import gzip
import io
import json
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import fitparse
    FITPARSE_AVAILABLE = True
except ImportError:
    FITPARSE_AVAILABLE = False

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


# ---------------------------------------------------------------------------
# FIT decoding helpers
# ---------------------------------------------------------------------------

def _decode_gear_change(data: int) -> dict:
    """Decode a packed gear_change_data uint32 from a Di2 shift event.

    Shimano Di2 packs gear information as:
      bits 0-7:   rear gear number (1 = smallest/hardest cog)
      bits 8-15:  front gear number (1 = inner/small ring)
      bits 16-23: rear gear teeth count
      bits 24-31: front gear teeth count
    """
    rear_gear_num = data & 0xFF
    front_gear_num = (data >> 8) & 0xFF
    rear_teeth = (data >> 16) & 0xFF
    front_teeth = (data >> 24) & 0xFF
    return {
        "rear_gear_num": rear_gear_num,
        "front_gear_num": front_gear_num,
        "rear_teeth": rear_teeth if rear_teeth > 0 else None,
        "front_teeth": front_teeth if front_teeth > 0 else None,
    }


def _decode_left_right_balance(value, is_100: bool = False) -> Optional[float]:
    """Decode Garmin's left_right_balance field to left power percentage.

    FIT has two formats:
    - left_right_balance (uint8): bit 7 = right, bits 0-6 = percentage
    - left_right_balance_100 / avg_left_right_balance (uint16): bit 15 = right, bits 0-14 = pct*100

    Set is_100=True for session/lap avg_left_right_balance fields.
    """
    if value is None:
        return None
    try:
        int_val = int(value)
        if is_100:
            right_dominant = bool(int_val & 0x8000)
            pct = (int_val & 0x7FFF) / 100.0
        else:
            right_dominant = bool(int_val & 0x80)
            pct = float(int_val & 0x7F)
        if right_dominant:
            return round(100.0 - pct, 1)
        return round(pct, 1)
    except (TypeError, ValueError):
        return None


def _get_field(message, *names):
    """Get the first matching field value from a FIT message."""
    for name in names:
        field = message.get_value(name)
        if field is not None:
            return field
    return None


def _semicircles_to_degrees(value) -> Optional[float]:
    """Convert FIT semicircle coordinates to decimal degrees."""
    if value is None:
        return None
    return round(value * (180.0 / 2**31), 6)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_fit_bytes(raw: bytes) -> bytes:
    """Extract raw FIT bytes from whatever Garmin's download endpoint returns.

    Garmin's ORIGINAL format download returns a ZIP archive containing one or
    more .fit files. Handle that, plus fall back for gzip and raw FIT.
    """
    if raw[:2] == b'PK':
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            fit_names = [n for n in zf.namelist() if n.lower().endswith('.fit')]
            if not fit_names:
                raise ValueError("ZIP archive contains no .fit files")
            return zf.read(fit_names[0])

    if raw[:2] == b'\x1f\x8b':
        return gzip.decompress(raw)

    return raw


def _safe_avg(values: list) -> Optional[float]:
    """Return mean of a list of numbers, or None if empty."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _safe_round(value, ndigits: int = 1) -> Optional[float]:
    if value is None:
        return None
    return round(value, ndigits)


# ---------------------------------------------------------------------------
# Power Duration Curve (Mean Maximal Power)
# ---------------------------------------------------------------------------

_PDC_DURATIONS_S = [5, 30, 60, 300, 600, 1200, 3600]
_PDC_LABELS = ["5s", "30s", "1min", "5min", "10min", "20min", "60min"]


def _compute_power_duration_curve(records: List[Dict]) -> Optional[Dict]:
    """Compute best mean maximal power for standard durations.

    Uses a sliding window over the per-second power values.
    O(n * num_durations) — efficient for rides up to ~8 hours.
    """
    powers = [r.get("power_w") for r in records]
    if not any(p is not None for p in powers):
        return None

    # Fill None gaps with 0 (coasting) for window computation
    filled = [p if p is not None else 0 for p in powers]
    n = len(filled)
    if n == 0:
        return None

    # Precompute prefix sums for O(1) window queries
    prefix = [0] * (n + 1)
    for i, p in enumerate(filled):
        prefix[i + 1] = prefix[i] + p

    curve: Dict[str, Any] = {}
    for duration_s, label in zip(_PDC_DURATIONS_S, _PDC_LABELS):
        if n < duration_s:
            continue
        best = 0
        for i in range(n - duration_s + 1):
            window_sum = prefix[i + duration_s] - prefix[i]
            if window_sum > best:
                best = window_sum
        best_avg = best / duration_s
        if best_avg > 0:
            curve[label] = round(best_avg)

    return curve if curve else None


# ---------------------------------------------------------------------------
# Climb detection + VAM
# ---------------------------------------------------------------------------

def _detect_climbs(
    records: List[Dict],
    min_elevation_gain_m: float = 50.0,
    min_avg_grade_pct: float = 3.0,
    min_duration_s: int = 120,
) -> List[Dict]:
    """Segment the ride into climbs based on sustained positive grade.

    A climb starts when grade rises above min_avg_grade_pct and ends when it
    drops back below it for more than 30 seconds.
    """
    if not records:
        return []

    climbs = []
    in_climb = False
    climb_start_idx = 0
    flat_count = 0
    FLAT_TOLERANCE_S = 30

    for i, rec in enumerate(records):
        grade = rec.get("grade_pct")
        if grade is None:
            continue

        if not in_climb:
            if grade >= min_avg_grade_pct:
                in_climb = True
                climb_start_idx = i
                flat_count = 0
        else:
            if grade < min_avg_grade_pct:
                flat_count += 1
                if flat_count > FLAT_TOLERANCE_S:
                    # End the climb at where the flat started
                    end_idx = i - flat_count
                    climb = _summarize_climb(records, climb_start_idx, end_idx)
                    if (climb.get("elevation_gain_m", 0) >= min_elevation_gain_m and
                            climb.get("duration_s", 0) >= min_duration_s):
                        climbs.append(climb)
                    in_climb = False
                    flat_count = 0
            else:
                flat_count = 0

    # Close any open climb at end of ride
    if in_climb:
        climb = _summarize_climb(records, climb_start_idx, len(records) - 1)
        if (climb.get("elevation_gain_m", 0) >= min_elevation_gain_m and
                climb.get("duration_s", 0) >= min_duration_s):
            climbs.append(climb)

    return climbs


def _summarize_climb(records: List[Dict], start_idx: int, end_idx: int) -> Dict:
    """Compute aggregate stats for a climb segment."""
    segment = records[start_idx:end_idx + 1]
    if not segment:
        return {}

    duration_s = len(segment)  # 1 record ≈ 1 second
    start_alt = segment[0].get("altitude_m")
    end_alt = segment[-1].get("altitude_m")
    elevation_gain = (end_alt - start_alt) if (start_alt is not None and end_alt is not None) else None

    powers = [r["power_w"] for r in segment if r.get("power_w") is not None]
    cadences = [r["cadence_rpm"] for r in segment if r.get("cadence_rpm") is not None]
    hrs = [r["heart_rate_bpm"] for r in segment if r.get("heart_rate_bpm") is not None]
    grades = [r["grade_pct"] for r in segment if r.get("grade_pct") is not None]
    speeds = [r["speed_mps"] for r in segment if r.get("speed_mps") is not None]

    result: Dict[str, Any] = {
        "start_time": segment[0].get("timestamp", ""),
        "end_time": segment[-1].get("timestamp", ""),
        "duration_s": duration_s,
    }

    if elevation_gain is not None:
        result["elevation_gain_m"] = round(elevation_gain, 1)
        if duration_s > 0:
            result["vam_m_per_hr"] = round((elevation_gain / duration_s) * 3600)

    if speeds:
        total_dist = sum(s for s in speeds)  # m/s * 1s = m per record
        result["distance_m"] = round(total_dist)

    if grades:
        result["avg_grade_pct"] = round(_safe_avg(grades), 1)
        result["max_grade_pct"] = round(max(grades), 1)

    if powers:
        result["avg_power_w"] = round(_safe_avg(powers))

    if cadences:
        result["avg_cadence_rpm"] = round(_safe_avg(cadences))

    if hrs:
        result["avg_hr_bpm"] = round(_safe_avg(hrs))

    return result


# ---------------------------------------------------------------------------
# Grade analysis
# ---------------------------------------------------------------------------

def _grade_analysis(records: List[Dict]) -> Optional[Dict]:
    """Bin per-second records by grade and report avg power/cadence/HR per bin."""
    bins = {
        "descending": {"label": "descending (<-3%)", "records": []},
        "flat": {"label": "flat (-3% to 3%)", "records": []},
        "gentle": {"label": "gentle (3-6%)", "records": []},
        "moderate": {"label": "moderate (6-9%)", "records": []},
        "steep": {"label": "steep (>9%)", "records": []},
    }

    has_grade = False
    for rec in records:
        grade = rec.get("grade_pct")
        if grade is None:
            continue
        has_grade = True
        if grade < -3:
            bucket = "descending"
        elif grade < 3:
            bucket = "flat"
        elif grade < 6:
            bucket = "gentle"
        elif grade < 9:
            bucket = "moderate"
        else:
            bucket = "steep"
        bins[bucket]["records"].append(rec)

    if not has_grade:
        return None

    result = {}
    for key, data in bins.items():
        recs = data["records"]
        if not recs:
            continue
        entry: Dict[str, Any] = {"time_s": len(recs)}
        powers = [r["power_w"] for r in recs if r.get("power_w") is not None]
        cadences = [r["cadence_rpm"] for r in recs if r.get("cadence_rpm") is not None]
        hrs = [r["heart_rate_bpm"] for r in recs if r.get("heart_rate_bpm") is not None]
        if powers:
            entry["avg_power_w"] = round(_safe_avg(powers))
        if cadences:
            entry["avg_cadence_rpm"] = round(_safe_avg(cadences))
        if hrs:
            entry["avg_hr_bpm"] = round(_safe_avg(hrs))
        result[key] = entry

    return result if result else None


# ---------------------------------------------------------------------------
# HR drift / cardiac drift (aerobic decoupling)
# ---------------------------------------------------------------------------

def _compute_hr_drift(records: List[Dict]) -> Optional[Dict]:
    """Compute aerobic decoupling (HR drift vs. power over the ride).

    Splits the ride into first and second halves by record count.
    Computes power:HR ratio for each half.
    Drift % = change in ratio from first to second half.
    Negative drift = HR increased relative to power (decoupling = less efficient).
    """
    MIN_RECORDS = 3600  # require ≥60 min of data

    filtered = [
        r for r in records
        if r.get("power_w") is not None and r.get("heart_rate_bpm") is not None
        and r["heart_rate_bpm"] > 0
    ]

    if len(filtered) < MIN_RECORDS:
        return None

    mid = len(filtered) // 2
    first_half = filtered[:mid]
    second_half = filtered[mid:]

    def power_hr_ratio(recs):
        avg_p = _safe_avg([r["power_w"] for r in recs])
        avg_hr = _safe_avg([r["heart_rate_bpm"] for r in recs])
        if avg_p is None or avg_hr is None or avg_hr == 0:
            return None
        return avg_p / avg_hr

    r1 = power_hr_ratio(first_half)
    r2 = power_hr_ratio(second_half)

    if r1 is None or r2 is None or r1 == 0:
        return None

    drift_pct = ((r2 - r1) / r1) * 100

    if abs(drift_pct) < 5:
        interpretation = "well_coupled"
    elif abs(drift_pct) < 10:
        interpretation = "moderate_drift"
    else:
        interpretation = "significant_decoupling"

    return {
        "hr_drift_pct": round(drift_pct, 1),
        "first_half_power_hr_ratio": round(r1, 3),
        "second_half_power_hr_ratio": round(r2, 3),
        "interpretation": interpretation,
        "note": "Negative drift = HR increased vs power (decoupling). >10% suggests aerobic base is limiting factor.",
    }


# ---------------------------------------------------------------------------
# Temperature correlation
# ---------------------------------------------------------------------------

def _compute_temperature_stats(records: List[Dict]) -> Optional[Dict]:
    """Summarize temperature and its relationship to HR and power."""
    temps = [r["temperature_c"] for r in records if r.get("temperature_c") is not None]
    if not temps:
        return None

    avg_temp = _safe_avg(temps)
    temp_min = min(temps)
    temp_max = max(temps)

    # Compare HR and power in coolest vs hottest third of records
    sorted_by_temp = sorted(
        [r for r in records if r.get("temperature_c") is not None],
        key=lambda r: r["temperature_c"]
    )
    third = max(1, len(sorted_by_temp) // 3)
    coolest = sorted_by_temp[:third]
    hottest = sorted_by_temp[-third:]

    cool_hr = _safe_round(_safe_avg([r["heart_rate_bpm"] for r in coolest if r.get("heart_rate_bpm")]))
    hot_hr = _safe_round(_safe_avg([r["heart_rate_bpm"] for r in hottest if r.get("heart_rate_bpm")]))
    cool_power = _safe_round(_safe_avg([r["power_w"] for r in coolest if r.get("power_w")]))
    hot_power = _safe_round(_safe_avg([r["power_w"] for r in hottest if r.get("power_w")]))

    result: Dict[str, Any] = {
        "avg_temp_c": _safe_round(avg_temp),
        "min_temp_c": temp_min,
        "max_temp_c": temp_max,
        "temp_range_c": round(temp_max - temp_min, 1),
    }
    if cool_hr is not None:
        result["avg_hr_coolest_third_bpm"] = cool_hr
    if hot_hr is not None:
        result["avg_hr_hottest_third_bpm"] = hot_hr
    if cool_power is not None:
        result["avg_power_coolest_third_w"] = cool_power
    if hot_power is not None:
        result["avg_power_hottest_third_w"] = hot_power

    return result


# ---------------------------------------------------------------------------
# Weight / W/kg lookup
# ---------------------------------------------------------------------------

def _get_rider_weight_kg(activity_date_str: str) -> Optional[float]:
    """Fetch rider weight from Garmin body composition for the given date."""
    if garmin_client is None:
        return None
    try:
        data = garmin_client.get_body_composition(activity_date_str)
        if not data:
            return None
        # Response is typically {"startDate": ..., "endDate": ..., "dateWeightList": [...]}
        weight_list = data.get("dateWeightList") or data.get("totalAverage") and [data]
        if not weight_list:
            # Try flat dict
            weight_g = data.get("weight") or data.get("totalWeightInGrams")
            if weight_g:
                return round(weight_g / 1000.0, 2)
            return None
        # Find closest entry to the activity date
        for entry in reversed(weight_list):
            weight_g = entry.get("weight") or entry.get("totalWeightInGrams")
            if weight_g:
                return round(weight_g / 1000.0, 2)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Shift summary
# ---------------------------------------------------------------------------

def _compute_shift_summary(shifts: list) -> dict:
    """Compute aggregate statistics over all shift events."""
    if not shifts:
        return {"total_shifts": 0, "note": "No DI2 shift events found in FIT file"}

    quality_counts: Dict[str, int] = {
        "proactive": 0, "reactive": 0, "coasting": 0, "spun_out": 0, "unknown": 0
    }
    terrain_quality: Dict[str, Dict[str, int]] = {
        "climbing": {"proactive": 0, "reactive": 0, "coasting": 0, "spun_out": 0, "unknown": 0},
        "flat": {"proactive": 0, "reactive": 0, "coasting": 0, "spun_out": 0, "unknown": 0},
        "descending": {"proactive": 0, "reactive": 0, "coasting": 0, "spun_out": 0, "unknown": 0},
    }
    front_shifts = 0
    rear_shifts = 0
    gear_usage: dict = {}
    cadences_at_shift = []

    # Burst detection: 3+ shifts within a 10-second window
    panic_bursts = 0
    burst_i = 0
    while burst_i < len(shifts):
        ts_i = shifts[burst_i].get("timestamp", "")
        cluster = [burst_i]
        for j in range(burst_i + 1, len(shifts)):
            ts_j = shifts[j].get("timestamp", "")
            try:
                dt_i = datetime.fromisoformat(ts_i)
                dt_j = datetime.fromisoformat(ts_j)
                if (dt_j - dt_i).total_seconds() <= 10.0:
                    cluster.append(j)
                else:
                    break
            except (ValueError, TypeError):
                break
        if len(cluster) >= 3:
            panic_bursts += 1
            burst_i = cluster[-1] + 1
        else:
            burst_i += 1

    for s in shifts:
        quality = s.get("quality", "unknown")
        quality_counts[quality] = quality_counts.get(quality, 0) + 1

        # Terrain classification
        grade = s.get("grade_at_shift_pct")
        if grade is not None:
            if grade >= 3:
                terrain = "climbing"
            elif grade <= -3:
                terrain = "descending"
            else:
                terrain = "flat"
        else:
            terrain = "flat"  # default
        terrain_quality[terrain][quality] = terrain_quality[terrain].get(quality, 0) + 1

        event = s.get("event", "")
        if "front" in event:
            front_shifts += 1
        else:
            rear_shifts += 1

        combo = s.get("gear_combo")
        if combo:
            gear_usage[combo] = gear_usage.get(combo, 0) + 1

        cad = s.get("cadence_at_shift_rpm")
        if cad is not None:
            cadences_at_shift.append(cad)

    total = len(shifts)
    proactive = quality_counts.get("proactive", 0)
    reactive = quality_counts.get("reactive", 0)

    summary: Dict[str, Any] = {
        "total_shifts": total,
        "front_shifts": front_shifts,
        "rear_shifts": rear_shifts,
        "proactive_shifts": proactive,
        "reactive_shifts": reactive,
        "coasting_shifts": quality_counts.get("coasting", 0),
        "spun_out_shifts": quality_counts.get("spun_out", 0),
        "proactive_pct": round(proactive / total * 100, 1) if total else 0,
        "reactive_pct": round(reactive / total * 100, 1) if total else 0,
        "panic_burst_episodes": panic_bursts,
        "gear_usage": dict(sorted(gear_usage.items(), key=lambda x: -x[1])),
    }

    # Include terrain breakdown only if grade data was present
    has_terrain_data = any(s.get("grade_at_shift_pct") is not None for s in shifts)
    if has_terrain_data:
        terrain_summary = {}
        for terrain, counts in terrain_quality.items():
            t_total = sum(counts.values())
            if t_total > 0:
                terrain_summary[terrain] = {
                    "total": t_total,
                    "proactive": counts.get("proactive", 0),
                    "reactive": counts.get("reactive", 0),
                    "reactive_pct": round(counts.get("reactive", 0) / t_total * 100, 1),
                }
        if terrain_summary:
            summary["by_terrain"] = terrain_summary

    if cadences_at_shift:
        summary["avg_cadence_at_shift_rpm"] = round(
            sum(cadences_at_shift) / len(cadences_at_shift), 1
        )
        summary["min_cadence_at_shift_rpm"] = min(cadences_at_shift)
        summary["max_cadence_at_shift_rpm"] = max(cadences_at_shift)

    return summary


# ---------------------------------------------------------------------------
# Main FIT parsing logic
# ---------------------------------------------------------------------------

def _parse_fit(fit_bytes: bytes, include_records: bool) -> dict:
    """Parse a FIT file and extract structured cycling data."""
    fit_bytes = _extract_fit_bytes(fit_bytes)
    fitfile = fitparse.FitFile(io.BytesIO(fit_bytes))

    session: Dict[str, Any] = {}
    laps: List[Dict] = []
    shifts: List[Dict] = []
    records: List[Dict] = []

    # Track last values for context at shift time
    last_cadence: Optional[float] = None
    last_grade: Optional[float] = None

    for message in fitfile.get_messages():
        msg_type = message.name

        # ------------------------------------------------------------------
        # Session summary
        # ------------------------------------------------------------------
        if msg_type == "session":
            session = {
                "sport": _get_field(message, "sport"),
                "sub_sport": _get_field(message, "sub_sport"),
                "start_time": str(_get_field(message, "start_time") or ""),
                "total_elapsed_time_s": _get_field(message, "total_elapsed_time"),
                "total_timer_time_s": _get_field(message, "total_timer_time"),
                "total_distance_m": _get_field(message, "total_distance"),
                "total_calories": _get_field(message, "total_calories"),
                "avg_speed_mps": _get_field(message, "avg_speed"),
                "max_speed_mps": _get_field(message, "max_speed"),
                "avg_power_w": _get_field(message, "avg_power"),
                "max_power_w": _get_field(message, "max_power"),
                "normalized_power_w": _get_field(message, "normalized_power"),
                "avg_cadence_rpm": _get_field(message, "avg_cadence"),
                "max_cadence_rpm": _get_field(message, "max_cadence"),
                "avg_heart_rate_bpm": _get_field(message, "avg_heart_rate"),
                "max_heart_rate_bpm": _get_field(message, "max_heart_rate"),
                "total_ascent_m": _get_field(message, "total_ascent"),
                "total_descent_m": _get_field(message, "total_descent"),
                "total_training_effect": _get_field(message, "total_training_effect"),
                "avg_left_power_phase_start_deg": _get_field(message, "avg_left_power_phase"),
                "avg_right_power_phase_start_deg": _get_field(message, "avg_right_power_phase"),
                "avg_left_pco_mm": _get_field(message, "avg_left_pco"),
                "avg_right_pco_mm": _get_field(message, "avg_right_pco"),
                "avg_left_torque_effectiveness_pct": _get_field(message, "avg_left_torque_effectiveness"),
                "avg_right_torque_effectiveness_pct": _get_field(message, "avg_right_torque_effectiveness"),
                "avg_left_pedal_smoothness_pct": _get_field(message, "avg_left_pedal_smoothness"),
                "avg_right_pedal_smoothness_pct": _get_field(message, "avg_right_pedal_smoothness"),
            }
            balance_raw = _get_field(message, "avg_left_right_balance")
            session["avg_left_power_pct"] = _decode_left_right_balance(balance_raw, is_100=True)
            if session["avg_left_power_pct"] is not None:
                session["avg_right_power_pct"] = round(100.0 - session["avg_left_power_pct"], 1)
            session = {k: v for k, v in session.items() if v is not None}

        # ------------------------------------------------------------------
        # Lap data
        # ------------------------------------------------------------------
        elif msg_type == "lap":
            lap: Dict[str, Any] = {
                "lap_number": len(laps) + 1,
                "start_time": str(_get_field(message, "start_time") or ""),
                "total_elapsed_time_s": _get_field(message, "total_elapsed_time"),
                "total_distance_m": _get_field(message, "total_distance"),
                "avg_speed_mps": _get_field(message, "avg_speed"),
                "avg_power_w": _get_field(message, "avg_power"),
                "normalized_power_w": _get_field(message, "normalized_power"),
                "avg_cadence_rpm": _get_field(message, "avg_cadence"),
                "avg_heart_rate_bpm": _get_field(message, "avg_heart_rate"),
                "avg_left_pco_mm": _get_field(message, "avg_left_pco"),
                "avg_right_pco_mm": _get_field(message, "avg_right_pco"),
                "avg_left_torque_effectiveness_pct": _get_field(message, "avg_left_torque_effectiveness"),
                "avg_right_torque_effectiveness_pct": _get_field(message, "avg_right_torque_effectiveness"),
                "avg_left_pedal_smoothness_pct": _get_field(message, "avg_left_pedal_smoothness"),
                "avg_right_pedal_smoothness_pct": _get_field(message, "avg_right_pedal_smoothness"),
                "total_ascent_m": _get_field(message, "total_ascent"),
                "total_descent_m": _get_field(message, "total_descent"),
            }
            balance_raw = _get_field(message, "avg_left_right_balance")
            left_pct = _decode_left_right_balance(balance_raw, is_100=True)
            if left_pct is not None:
                lap["avg_left_power_pct"] = left_pct
                lap["avg_right_power_pct"] = round(100.0 - left_pct, 1)
            lap = {k: v for k, v in lap.items() if v is not None}

            # Variability Index per lap
            np_w = lap.get("normalized_power_w")
            avg_w = lap.get("avg_power_w")
            if np_w and avg_w and avg_w > 0:
                lap["variability_index"] = round(np_w / avg_w, 3)

            laps.append(lap)

        # ------------------------------------------------------------------
        # DI2 / electronic shifting events
        # ------------------------------------------------------------------
        elif msg_type == "event":
            event_type = _get_field(message, "event")
            if event_type in ("rear_gear_change", "front_gear_change", "gear_change"):
                gear_data_raw = _get_field(message, "gear_change_data", "data")
                timestamp = _get_field(message, "timestamp")

                shift_entry: Dict[str, Any] = {
                    "timestamp": str(timestamp or ""),
                    "event": str(event_type),
                    "cadence_at_shift_rpm": last_cadence,
                }

                if last_grade is not None:
                    shift_entry["grade_at_shift_pct"] = round(last_grade, 1)

                if gear_data_raw is not None:
                    try:
                        decoded = _decode_gear_change(int(gear_data_raw))
                        shift_entry.update(decoded)
                        ft = decoded.get("front_teeth")
                        rt = decoded.get("rear_teeth")
                        if ft and rt:
                            shift_entry["gear_combo"] = f"{ft}/{rt}t"
                    except (TypeError, ValueError):
                        shift_entry["gear_change_data_raw"] = str(gear_data_raw)

                # Classify shift quality
                cad = last_cadence
                if cad is not None:
                    if cad == 0:
                        shift_entry["quality"] = "coasting"
                    elif cad < 70:
                        shift_entry["quality"] = "reactive"
                    elif cad > 100:
                        shift_entry["quality"] = "spun_out"
                    else:
                        shift_entry["quality"] = "proactive"
                else:
                    shift_entry["quality"] = "unknown"

                shift_entry = {k: v for k, v in shift_entry.items() if v is not None}
                shifts.append(shift_entry)

        # ------------------------------------------------------------------
        # Per-second records
        # ------------------------------------------------------------------
        elif msg_type == "record":
            cadence = _get_field(message, "cadence")
            if cadence is not None:
                last_cadence = cadence

            grade = _get_field(message, "grade")
            if grade is not None:
                last_grade = grade

            record: Dict[str, Any] = {
                "timestamp": str(_get_field(message, "timestamp") or ""),
                "power_w": _get_field(message, "power"),
                "cadence_rpm": cadence,
                "heart_rate_bpm": _get_field(message, "heart_rate"),
                "speed_mps": _get_field(message, "enhanced_speed", "speed"),
                "altitude_m": _get_field(message, "enhanced_altitude", "altitude"),
                "grade_pct": grade,
                "temperature_c": _get_field(message, "temperature"),
                "lat_deg": _semicircles_to_degrees(_get_field(message, "position_lat")),
                "lon_deg": _semicircles_to_degrees(_get_field(message, "position_long")),
                "left_pco_mm": _get_field(message, "left_pco"),
                "right_pco_mm": _get_field(message, "right_pco"),
                "left_torque_effectiveness_pct": _get_field(message, "left_torque_effectiveness"),
                "right_torque_effectiveness_pct": _get_field(message, "right_torque_effectiveness"),
                "left_pedal_smoothness_pct": _get_field(message, "left_pedal_smoothness"),
                "right_pedal_smoothness_pct": _get_field(message, "right_pedal_smoothness"),
            }
            balance_raw = _get_field(message, "left_right_balance")
            left_pct = _decode_left_right_balance(balance_raw)
            if left_pct is not None:
                record["left_power_pct"] = left_pct
                record["right_power_pct"] = round(100.0 - left_pct, 1)

            left_phase = _get_field(message, "left_power_phase")
            if left_phase:
                try:
                    record["left_power_phase_start_deg"] = left_phase[0]
                    record["left_power_phase_end_deg"] = left_phase[1]
                except (IndexError, TypeError):
                    pass
            right_phase = _get_field(message, "right_power_phase")
            if right_phase:
                try:
                    record["right_power_phase_start_deg"] = right_phase[0]
                    record["right_power_phase_end_deg"] = right_phase[1]
                except (IndexError, TypeError):
                    pass

            record = {k: v for k, v in record.items() if v is not None}
            records.append(record)

    # ------------------------------------------------------------------
    # Post-parse enrichment
    # ------------------------------------------------------------------

    # Variability Index for session
    np_w = session.get("normalized_power_w")
    avg_w = session.get("avg_power_w")
    if np_w and avg_w and avg_w > 0:
        session["variability_index"] = round(np_w / avg_w, 3)

    shift_summary = _compute_shift_summary(shifts)

    # Record-based analytics (computed from per-second data regardless of include_records flag)
    grade_stats = _grade_analysis(records) if records else None
    hr_drift = _compute_hr_drift(records) if records else None
    temp_stats = _compute_temperature_stats(records) if records else None
    climbs = _detect_climbs(records) if records else []
    pdc = _compute_power_duration_curve(records) if records else None

    if grade_stats:
        session["grade_analysis"] = grade_stats
    if hr_drift:
        session["hr_drift"] = hr_drift
    if temp_stats:
        session["temperature_stats"] = temp_stats

    result: Dict[str, Any] = {
        "session": session,
        "laps": laps,
        "shift_summary": shift_summary,
        "shifts": shifts,
    }

    if climbs:
        result["climbs"] = climbs

    if pdc:
        result["power_duration_curve"] = pdc

    if include_records:
        result["records"] = records

    return result


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------

def register_tools(app):
    """Register all activity analysis tools with the MCP server app"""

    @app.tool()
    async def get_activity_fit_data(
        activity_id: int,
        include_records: bool = False,
    ) -> str:
        """Download and parse FIT file for an activity to expose advanced cycling data.

        Returns data not available through the standard REST API, including:
        - DI2 / electronic shifting events with cadence at time of shift, grade at shift,
          gear combinations, shift quality classification, and terrain-grouped shift analysis
        - Cycling dynamics per session and lap: platform center offset (PCO), left/right power
          balance, torque effectiveness, pedal smoothness
        - Variability Index (NP / avg_power) per session and lap
        - Climb detection with VAM (vertical ascent rate), avg power/cadence/HR per climb,
          and W/kg per climb (using auto-fetched body weight from Garmin)
        - Grade-correlated stats: avg power, cadence, HR broken down by terrain steepness
        - HR drift / cardiac drift coefficient (aerobic decoupling for rides ≥60 min)
        - Temperature correlation: avg HR/power in hottest vs. coolest portions of ride
        - Power Duration Curve: best mean maximal power at 5s, 30s, 1min, 5min, 10min, 20min, 60min
        - Optional full per-second time series when include_records=True

        Shift quality:
        - proactive: shifted at 70-100 rpm (ideal cadence range)
        - reactive: shifted below 70 rpm (already grinding before shifting)
        - coasting: shifted at 0 rpm (mid-stop or freewheeling)
        - spun_out: shifted above 100 rpm (waited too long in easy gear)

        Note: DI2 data requires Shimano Di2 / SRAM eTap. Cycling dynamics require a
        compatible power meter (e.g., Garmin Rally, Favero Assioma, PowerTap P1 pedals).

        Args:
            activity_id: Garmin activity ID
            include_records: Include full per-second time series (default False).
                             Warning: adds significant data volume for long rides.
        """
        if not FITPARSE_AVAILABLE:
            return (
                "fitparse library is not installed. "
                "Install it with: pip install fitparse"
            )

        try:
            from garminconnect import Garmin

            fit_bytes = garmin_client.download_activity(
                activity_id,
                dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
            )

            if not fit_bytes:
                return f"No FIT data returned for activity {activity_id}"

            raw = bytes(fit_bytes)

            try:
                parsed = _parse_fit(raw, include_records=include_records)
            except Exception as parse_err:
                return json.dumps({
                    "error": str(parse_err),
                    "debug": {
                        "total_bytes": len(raw),
                        "first_16_bytes_hex": raw[:16].hex(),
                        "hint": (
                            "1f8b = gzip, 504b = ZIP, 0e10/0c10 = raw FIT, "
                            "3c or 7b = HTML/JSON error from Garmin"
                        ),
                    }
                }, indent=2)

            parsed["activity_id"] = activity_id

            # W/kg: fetch body weight matched to activity date
            start_time_str = parsed.get("session", {}).get("start_time", "")
            activity_date = start_time_str[:10] if start_time_str else None
            if activity_date:
                weight_kg = _get_rider_weight_kg(activity_date)
                if weight_kg:
                    parsed["rider_weight_kg"] = weight_kg
                    # W/kg for session avg power and NP
                    avg_w = parsed["session"].get("avg_power_w")
                    np_w = parsed["session"].get("normalized_power_w")
                    if avg_w:
                        parsed["session"]["avg_w_per_kg"] = round(avg_w / weight_kg, 2)
                    if np_w:
                        parsed["session"]["normalized_w_per_kg"] = round(np_w / weight_kg, 2)
                    # W/kg per climb
                    for climb in parsed.get("climbs", []):
                        climb_w = climb.get("avg_power_w")
                        if climb_w:
                            climb["avg_w_per_kg"] = round(climb_w / weight_kg, 2)

            return json.dumps(parsed, indent=2, default=str)

        except Exception as e:
            return f"Error downloading FIT data for activity {activity_id}: {str(e)}"

    @app.tool()
    async def get_power_duration_curve(
        num_activities: int = 20,
        activity_type: str = "cycling",
    ) -> str:
        """Get season-best Power Duration Curve across recent activities.

        Downloads FIT files for recent cycling activities and computes best mean maximal
        power at each standard duration. Returns season bests with which activity and date
        each best came from.

        Durations: 5s (sprint), 30s, 1min, 5min (VO2 max proxy), 10min, 20min (FTP proxy), 60min

        Use the 20-minute best × 0.95 as a strong FTP estimate without a formal test.

        Warning: downloads multiple FIT files — may take 30-60 seconds for 20 activities.

        Args:
            num_activities: Number of recent activities to analyze (default 20, max 50)
            activity_type: Activity type to filter (default "cycling")
        """
        if not FITPARSE_AVAILABLE:
            return "fitparse library is not installed. Install it with: pip install fitparse"

        MAX_ACTIVITIES = 50
        num_activities = min(num_activities, MAX_ACTIVITIES)

        try:
            from garminconnect import Garmin

            # Fetch recent activities
            activities = garmin_client.get_activities(0, num_activities)
            if not activities:
                return "No activities found."

            # Filter by sport type
            cycling_activities = [
                a for a in activities
                if activity_type.lower() in str(a.get("activityType", {}).get("typeKey", "")).lower()
                or activity_type.lower() in str(a.get("activityType", {}).get("parentTypeId", "")).lower()
                or "cycling" in str(a.get("activityType", {}).get("typeKey", "")).lower()
                or "biking" in str(a.get("activityType", {}).get("typeKey", "")).lower()
            ]

            if not cycling_activities:
                return f"No {activity_type} activities found in the last {num_activities} activities."

            # Season bests: duration_label -> {watts, activity_id, date}
            season_bests: Dict[str, Any] = {}
            processed = 0
            errors = 0

            for activity in cycling_activities:
                act_id = activity.get("activityId")
                act_date = str(activity.get("startTimeLocal", ""))[:10]
                if not act_id:
                    continue

                try:
                    fit_bytes = garmin_client.download_activity(
                        act_id,
                        dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
                    )
                    if not fit_bytes:
                        continue

                    raw = bytes(fit_bytes)
                    extracted = _extract_fit_bytes(raw)
                    fitfile = fitparse.FitFile(io.BytesIO(extracted))

                    # Extract just the power records
                    act_records = []
                    for msg in fitfile.get_messages("record"):
                        p = msg.get_value("power")
                        act_records.append({"power_w": p})

                    pdc = _compute_power_duration_curve(act_records)
                    if pdc:
                        for label, watts in pdc.items():
                            if label not in season_bests or watts > season_bests[label]["watts"]:
                                season_bests[label] = {
                                    "watts": watts,
                                    "activity_id": act_id,
                                    "date": act_date,
                                }
                    processed += 1

                except Exception:
                    errors += 1
                    continue

            if not season_bests:
                return f"No power data found across {processed} activities."

            # Order by duration
            ordered = {label: season_bests[label] for label in _PDC_LABELS if label in season_bests}

            # FTP estimate from 20min best
            ftp_estimate = None
            if "20min" in ordered:
                ftp_estimate = round(ordered["20min"]["watts"] * 0.95)

            return json.dumps({
                "activities_analyzed": processed,
                "activities_skipped": errors,
                "ftp_estimate_w": ftp_estimate,
                "ftp_note": "Estimated as 95% of 20-minute best power",
                "season_bests": ordered,
            }, indent=2)

        except Exception as e:
            return f"Error computing power duration curve: {str(e)}"

    return app

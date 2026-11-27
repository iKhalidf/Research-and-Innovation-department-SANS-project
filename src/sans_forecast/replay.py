"""Build a retrospective replay from a complete UTC day's departure counts."""

import csv
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import io
import json
import math
from pathlib import Path, PureWindowsPath

RUNWAYS = ("34C", "34L")
DATE_HOURS = 24
INITIAL_HOURS = 12
TOLERANCE = 2
MODEL_ID = "amazon/chronos-2"
MODEL_REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"
RUNTIME = "chronos-forecasting"
RUNTIME_VERSION = "2.3.2"
QUANTILE = 0.5


def utc_hours(day: date) -> tuple[str, ...]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return tuple(
        (start + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for hour in range(DATE_HOURS)
    )


def source_info(kind: str, name: str, raw: bytes) -> dict:
    definition = (
        "Accepted A2 departure candidates for 34C and 34L, binned by UTC event anchor."
        if kind == "a2"
        else "Synthetic departure counts for a demonstration."
    )
    return {"kind": kind, "name": name, "sha256": sha256(raw).hexdigest(), "definition": definition}


def counts_from_csv(path: Path, day: date, kind: str) -> tuple[tuple[int, ...], dict]:
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != ["hour_utc", "count"]:
        raise ValueError("Hourly CSV must have the columns hour_utc,count in that order.")
    expected = utc_hours(day)
    counts = {}
    for row in reader:
        hour = row["hour_utc"]
        count = row["count"]
        if hour not in expected or hour in counts:
            raise ValueError(f"Hourly CSV row {reader.line_num} has an unexpected or duplicate UTC hour.")
        if None in row or count is None or not count.isascii() or not count.isdecimal():
            raise ValueError(f"Hourly CSV row {reader.line_num} must have a nonnegative integer count.")
        counts[hour] = int(count)
    missing = [hour for hour in expected if hour not in counts]
    if missing:
        raise ValueError(f"Hourly CSV is missing {len(missing)} UTC hour(s), starting with {missing[0]}.")
    return tuple(counts[hour] for hour in expected), source_info(kind, "Hourly count CSV", raw)


def verify_provider_hours(preflight: Path, day: date) -> str:
    raw = preflight.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("inputs"), list):
        raise ValueError("A2 preflight must list the provider input partitions.")
    expected = {f"wingbits_{day.isoformat()}_{hour:02d}.parquet" for hour in range(DATE_HOURS)}
    partitions = Counter(
        PureWindowsPath(item["file"]).name
        for item in manifest["inputs"]
        if isinstance(item, dict)
        and isinstance(item.get("file"), str)
        and type(item.get("rows")) is int
        and item["rows"] > 0
    )
    if partitions != Counter({partition: 1 for partition in expected}) or len(manifest["inputs"]) != DATE_HOURS:
        raise ValueError("A2 preflight must confirm exactly one nonempty provider partition for each UTC hour.")
    return sha256(raw).hexdigest()


def counts_from_flights(path: Path, preflight: Path, day: date) -> tuple[tuple[int, ...], dict]:
    preflight_sha256 = verify_provider_hours(preflight, day)
    raw = path.read_bytes()
    flights = json.loads(raw)
    if not isinstance(flights, list):
        raise ValueError("A2 flights.json must be a list of flight candidates.")
    start_ms = int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp() * 1000)
    hour_ms = 3_600_000
    counts = [0] * DATE_HOURS
    seen = set()
    for flight in flights:
        if not isinstance(flight, dict) or not isinstance(flight.get("runway_status"), str):
            raise ValueError("A2 candidates must provide runway_status.")
        if flight["runway_status"] != "accepted_departure_candidate" or flight.get("runway") not in RUNWAYS:
            continue
        anchor = flight.get("anchor_ms")
        flight_id = flight.get("flight_id")
        if type(anchor) is not int or not isinstance(flight_id, str) or not flight_id:
            raise ValueError("Accepted A2 candidates must provide flight_id and integer anchor_ms.")
        if flight_id in seen:
            raise ValueError("A2 flights.json contains duplicate accepted flight IDs.")
        seen.add(flight_id)
        hour = (anchor - start_ms) // hour_ms
        if 0 <= hour < DATE_HOURS:
            counts[hour] += 1
    source = source_info("a2", "A2 flight events", raw)
    source["preflight_sha256"] = preflight_sha256
    return tuple(counts), source


def round_count(prediction: float) -> int:
    if not math.isfinite(prediction):
        raise ValueError("Forecast model produced a nonfinite count.")
    return math.floor(max(0, prediction) + 0.5)


def score_predictions(hours: Sequence[dict], key: str) -> dict:
    errors = [abs(row[key] - row["actual"]) for row in hours]
    within = sum(error <= TOLERANCE for error in errors)
    return {
        "mae": round(sum(errors) / len(errors), 2),
        "within_tolerance": within,
        "total": len(errors),
        "within_tolerance_pct": round(100 * within / len(errors), 2),
    }


def build_replay(
    day: date,
    counts: Sequence[int],
    source: dict,
    predict: Callable[[Sequence[int]], float],
    device: str,
) -> dict:
    if len(counts) != DATE_HOURS or any(type(count) is not int or count < 0 for count in counts):
        raise ValueError("Replay requires 24 nonnegative integer departure counts.")
    hours = utc_hours(day)
    rows = []
    for index in range(INITIAL_HOURS, DATE_HOURS):
        history = tuple(counts[:index])
        rows.append({
            "hour_utc": hours[index],
            "issued_at_utc": hours[index],
            "actual": counts[index],
            "predicted": round_count(predict(history)),
            "baseline": history[-1],
        })
    return {
        "schema_version": 1,
        "date_utc": day.isoformat(),
        "source": source,
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "runtime": RUNTIME,
            "runtime_version": RUNTIME_VERSION,
            "quantile": QUANTILE,
            "device": device,
        },
        "protocol": {
            "kind": "retrospective_one_hour_ahead",
            "initial_context_hours": INITIAL_HOURS,
            "horizon_hours": 1,
            "tolerance_departures": TOLERANCE,
            "runways": list(RUNWAYS),
            "rounding": "nonnegative_half_up",
            "baseline": "previous_hour",
        },
        "context": [{"hour_utc": hour, "actual": counts[index]} for index, hour in enumerate(hours[:INITIAL_HOURS])],
        "hours": rows,
        "scores": {
            "model": score_predictions(rows, "predicted"),
            "baseline": score_predictions(rows, "baseline"),
        },
    }

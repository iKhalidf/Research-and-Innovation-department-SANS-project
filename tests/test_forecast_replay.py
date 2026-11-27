"""Independent checks for the forecast replay's chronology and data boundary."""

from datetime import date, datetime
from hashlib import sha256
import json

import pytest

from sans_forecast.replay import build_replay, counts_from_csv, counts_from_flights


DAY = date(2026, 10, 1)


def test_one_hour_ahead_replay_uses_only_completed_hours():
    counts = tuple(range(12)) + (7, 9, 12) + (7,) * 9
    forecasts = (-1.0, 6.5, 11.5, 8.49) + (7.0,) * 8
    seen = []

    def predict(history):
        position = len(seen)
        assert history == counts[:12 + position]
        seen.append(history)
        return forecasts[position]

    replay = build_replay(DAY, counts, {"kind": "synthetic"}, predict, "cpu")

    assert [len(history) for history in seen] == list(range(12, 24))
    assert [row["actual"] for row in replay["hours"]] == list(counts[12:])
    assert [row["baseline"] for row in replay["hours"]] == list(counts[11:23])
    assert [row["predicted"] for row in replay["hours"]] == [0, 7, 12, 8] + [7] * 8
    assert replay["hours"][0]["issued_at_utc"] == "2026-10-01T12:00:00Z"
    assert replay["hours"][-1]["hour_utc"] == "2026-10-01T23:00:00Z"
    assert replay["scores"] == {
        "model": {"mae": 0.83, "within_tolerance": 11, "total": 12, "within_tolerance_pct": 91.67},
        "baseline": {"mae": 1.17, "within_tolerance": 9, "total": 12, "within_tolerance_pct": 75.0},
    }


@pytest.mark.parametrize("defect", ["missing", "duplicate"])
def test_hourly_csv_distinguishes_explicit_zeroes_from_invalid_coverage(tmp_path, defect):
    csv_path = tmp_path / "hourly.csv"
    rows = [f"2026-10-01T{hour:02}:00:00Z,{hour}" for hour in range(24)]
    csv_path.write_text("hour_utc,count\n" + "\n".join(reversed(rows)), encoding="utf-8")
    counts, source = counts_from_csv(csv_path, DAY, "synthetic")
    assert counts == tuple(range(24))
    assert source["kind"] == "synthetic"

    rows.pop()
    if defect == "duplicate":
        rows.append(rows[-1])
    csv_path.write_text("hour_utc,count\n" + "\n".join(rows), encoding="utf-8")
    with pytest.raises(ValueError, match=defect):
        counts_from_csv(csv_path, DAY, "synthetic")


@pytest.fixture
def a2_inputs(tmp_path):
    inventories = [
        {"file": str(tmp_path / f"wingbits_2026-10-01_{hour:02}.parquet"), "rows": 1}
        for hour in range(24)
    ]

    def event(index, anchor, runway="34C", status="accepted_departure_candidate"):
        return {
            "flight_id": f"private-fixture-flight-{index}",
            "callsigns": ["PRIVATE-FIXTURE-CALLSIGN"],
            "anchor_ms": anchor,
            "runway": runway,
            "runway_status": status,
        }

    start = int(datetime.fromisoformat("2026-10-01T00:00:00+00:00").timestamp() * 1000)
    flights = [
        event(0, start - 1),
        event(1, start),
        event(2, start + 3_599_999, runway="34L"),
        event(3, start + 3_600_000),
        event(4, start + 3_600_000, runway="34R"),
        event(5, start + 3_600_000, status="ambiguous_runway"),
        event(6, start + 86_400_000),
    ]
    flights_path = tmp_path / "private-flight-events.json"
    flights_path.write_text(json.dumps(flights), encoding="utf-8")
    return flights_path, inventories


def test_a2_counts_keep_only_accepted_in_scope_events_and_publish_no_identities(a2_inputs):
    flights_path, inventories = a2_inputs
    preflight = flights_path.with_name("preflight.json")
    preflight.write_text(json.dumps({"inputs": inventories}), encoding="utf-8")

    counts, source = counts_from_flights(flights_path, preflight, DAY)
    assert counts == (2, 1) + (0,) * 22
    assert source["sha256"] == sha256(flights_path.read_bytes()).hexdigest()
    assert source["preflight_sha256"] == sha256(preflight.read_bytes()).hexdigest()

    public = json.dumps(build_replay(DAY, counts, source, lambda history: 0, "cpu"))
    for private_value in ("private-fixture-flight", "PRIVATE-FIXTURE-CALLSIGN", flights_path.name, "anchor_ms"):
        assert private_value not in public


@pytest.mark.parametrize("defect", ["missing", "duplicate", "empty"])
def test_a2_rejects_incomplete_provider_inventory(a2_inputs, defect):
    flights_path, inventories = a2_inputs
    preflight = flights_path.with_name("preflight.json")
    affected = inventories.pop(6)
    if defect == "duplicate":
        inventories.append(inventories[-1])
    elif defect == "empty":
        inventories.append({**affected, "rows": 0})
    preflight.write_text(json.dumps({"inputs": inventories}), encoding="utf-8")
    with pytest.raises(ValueError, match="one nonempty provider partition"):
        counts_from_flights(flights_path, preflight, DAY)



def test_local_server_exposes_only_the_named_replay_and_scenario_contours(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import urlopen

    from sans_a2.server import handler_for

    private_input = tmp_path / "flights.json"
    private_input.write_text('{"private": true}', encoding="utf-8")
    output = tmp_path / "forecast-replay.json"
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for({"output_dir": tmp_path}))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as unavailable:
            urlopen(base + "/forecast-data/replay.json", timeout=5)
        assert unavailable.value.code == 404

        output.write_text('{"aggregate": true}', encoding="utf-8")
        with urlopen(base + "/forecast-data/replay.json", timeout=5) as response:
            assert json.load(response) == {"aggregate": True}
            assert response.headers["Cache-Control"] == "no-store"
        with urlopen(base + "/map-2/scenarios/34C_BOMOX2W_A320.geojson", timeout=5) as response:
            assert response.headers.get_content_type() == "application/geo+json"

        for path in (
            "/flights.json",
            "/forecast-data/flights.json",
            "/forecast-data/%2e%2e/flights.json",
            "/map-2/data/../../flights.json",
        ):
            with pytest.raises(HTTPError) as forbidden:
                urlopen(base + path, timeout=5)
            assert forbidden.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

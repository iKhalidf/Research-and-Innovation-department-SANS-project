"""Run the optional Masar forecast replay generator."""

import argparse
from datetime import date
import json
from pathlib import Path
import tempfile

from .chronos import chronos_predictor
from .replay import build_replay, counts_from_csv, counts_from_flights


def write_replay(path: Path, replay: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(replay, indent=2, allow_nan=False) + "\n"
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            staged = Path(stream.name)
            stream.write(payload)
        staged.replace(path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--flights", type=Path, help="Local A2 flights.json.")
    inputs.add_argument("--counts", type=Path, help="Complete UTC day of hourly counts as hour_utc,count CSV.")
    parser.add_argument("--preflight", type=Path, help="A2 input inventory, defaults to preflight.json beside --flights.")
    parser.add_argument("--date", required=True, help="UTC day in YYYY-MM-DD format.")
    parser.add_argument("--output", required=True, type=Path, help="Destination JSON file.")
    parser.add_argument("--source", choices=("a2", "synthetic"), help="Required provenance for --counts.")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu", help="Inference device.")
    args = parser.parse_args()
    if args.flights and args.source not in (None, "a2"):
        parser.error("--flights requires --source a2.")
    if args.counts and not args.source:
        parser.error("--counts requires --source a2 or --source synthetic.")
    if args.counts and args.preflight:
        parser.error("--preflight requires --flights.")
    if args.output.name.lower() == "forecast-example.json" and args.source != "synthetic":
        parser.error("forecast-example.json is reserved for synthetic counts.")
    try:
        day = date.fromisoformat(args.date)
        if day.isoformat() != args.date:
            raise ValueError("Use YYYY-MM-DD for --date.")
        source_path = args.flights or args.counts
        preflight = args.preflight or source_path.with_name("preflight.json")
        reserved = [source_path, preflight] if args.flights else [source_path]
        if args.output.resolve() in {path.resolve() for path in reserved}:
            raise ValueError("The output path must be separate from the inputs.")
        if args.flights:
            counts, source = counts_from_flights(source_path, preflight, day)
        else:
            counts, source = counts_from_csv(source_path, day, args.source)
        replay = build_replay(day, counts, source, chronos_predictor(args.device), args.device)
        write_replay(args.output, replay)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Forecast Replay: {exc}\n")
    print(f"Wrote {args.output}")
    for label, stats in replay["scores"].items():
        print(f"{label}: MAE {stats['mae']:.2f}; {stats['within_tolerance']}/{stats['total']} within ±{replay['protocol']['tolerance_departures']}")


if __name__ == "__main__":
    main()

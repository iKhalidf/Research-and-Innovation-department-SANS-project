"""Run one validated simulation job in a subprocess owned by the local server."""

from functools import partial
from hashlib import file_digest
from importlib.metadata import version
import json
from pathlib import Path
import sys

from .cases import ROOT, resolve_cases


INPUTS = {
    "A1 published procedures": "data/A1_Procedures.csv",
    "A5 processed aircraft noise": "noise-population/data/a5/processed/output_manifest.csv",
    "A6 WorldPop receptors": "noise-population/data/a6/processed/a6_receptors.parquet",
    "Runway reference": "noise-population/data/external/a1_external_airport_runways.csv",
    "Noise configuration": "noise-population/config/noise.yaml",
    "OpenStreetMap place points": "map-1/OEJN-ground.gpkg",
    "Scenario generator": "tools/scenarios/generate_scenarios.py",
}


def run_cases(values, work, report):
    sys.path.insert(0, str(ROOT))
    from tools.scenarios.generate_scenarios import CO2_PER_KG_FUEL, calculate_scenarios, git_head

    cases = resolve_cases(values)
    sources = []
    for label, source in INPUTS.items():
        with (ROOT / source).open("rb") as file:
            sources.append({"label": label, "path": source, "sha256": file_digest(file, "sha256").hexdigest()})
    noise_repo = ROOT / "noise-population"
    models = {
        "fuel": f"OpenAP {version('openap')} WRAP + FuelFlow",
        "noise": "ECAC Doc 29, 5th edition with EASA ANP 2.3",
        "noise_code_commit": git_head(noise_repo),
        "population": "WorldPop R2025A, approximately 100 m cells",
    }
    items = []
    calculated = {}
    for i, case in enumerate(cases):
        case_progress = partial(report, case=i + 1)
        combo = (case["runway"], case["sid"], case["aircraft"])
        mass = case["mass_pct"] / 100
        temperature = case["temperature_c"]
        key = (*combo, mass, temperature)
        if key not in calculated:
            calculated[key], = calculate_scenarios(
                [combo], noise_repo, work / f"case-{i + 1}",
                mass_fraction=mass, temperature_c=temperature, progress=case_progress,
            )
        else:
            case_progress("reuse")
        record, contour, info, noise = calculated[key]
        fuel = float(info["to10k"]["fuel_kg"])
        count = case["count"]
        case_progress("co2", factor=CO2_PER_KG_FUEL)
        items.append({
            "inputs": {**case, "mass_kg": info["mass_kg"],
                       "temperature_c": float(noise["t_c"]), "humidity_pct": float(noise["rh_pct"]),
                       "qnh_hpa": float(noise["qnh_hpa"]), "wind_kt": float(noise["wind_kt"])},
            "scenario": record, "contour": contour,
            "plan_fuel_kg": round(fuel * count, 1),
            "plan_co2_kg": round(fuel * CO2_PER_KG_FUEL * count, 1),
            "noise_flags": str(noise.get("flags", "")).split(";") if isinstance(noise.get("flags"), str) else [],
        })
    report("finalizing")
    difference = None
    if len(items) == 2:
        first, second = items
        difference = {
            "fuel_kg": round(second["plan_fuel_kg"] - first["plan_fuel_kg"], 1),
            "co2_kg": round(second["plan_co2_kg"] - first["plan_co2_kg"], 1),
            "residents": {level: count - first["scenario"]["residents"][level]
                          for level, count in second["scenario"]["residents"].items()},
        }
    return {"cases": items, "difference": difference, "models": models, "sources": sources}


def main():
    work = Path(sys.argv[1]).resolve()

    def report(stage, **details):
        with (work / "progress.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"stage": stage, **details}) + "\n")

    try:
        cases = json.loads((work / "request.json").read_text(encoding="utf-8"))
        result = run_cases(cases, work, report)
        serialized = json.dumps({"result": result}, ensure_ascii=False, allow_nan=False)
    except (ImportError, OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Ask Masar calculation failed: {exc}", file=sys.stderr, flush=True)
        return 1
    (work / "response.json").write_text(serialized, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

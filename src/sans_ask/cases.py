"""Resolve assistant proposals against Masar's covered procedures and aircraft."""

import csv
from functools import lru_cache
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("runway", "sid", "aircraft", "count", "mass_pct", "temperature_c")
AIRCRAFT_ALIASES = {
    "A320": "A320", "AIRBUSA320": "A320", "A320214": "A320",
    "A20N": "A20N", "A320NEO": "A20N", "AIRBUSA320NEO": "A20N",
    "B77W": "B77W", "B777300ER": "B77W", "777300ER": "B77W", "BOEING777300ER": "B77W",
}
EXPLANATIONS = {
    "fuel": "OpenAP estimates fuel from brake release to 10,000 ft on a standard climb. Plan fuel is the fuel per departure multiplied by the departure count.",
    "noise": "LAmax is the estimated peak sound level of one modelled departure. The 60, 65 and 70 dB contours show where that departure reaches each threshold. Resident counts refer to people living inside each footprint.",
    "assumptions": "Takeoff mass affects the fuel calculation; temperature affects the noise calculation. The planned trajectory retains the standard climb and still-air assumptions. These are modelled cases.",
    "supported": "Ask for one or two departures or plans using a published route from 34C or 34L and an A320, A320neo (A20N), or 777-300ER (B77W).",
}


@lru_cache(maxsize=1)
def route_catalog():
    with (ROOT / "data/A1_Procedures.csv").open(encoding="utf-8-sig", newline="") as source:
        return sorted({(row["departure_runway"], row["procedure_name"]) for row in csv.DictReader(source)
                       if row["departure_runway"] in {"34C", "34L"}})


@lru_cache(maxsize=3)
def aircraft_weights(aircraft):
    from openap import prop

    data = prop.aircraft(aircraft)
    return float(data["oew"]), float(data["mtow"])


def compact(value):
    return re.sub(r"[\s-]+", "", value.upper())


def route_question(routes):
    choices = " or ".join(f"{name} on {runway}" for runway, name in routes)
    return f"Which departure route did you mean? Choose {choices}."


def ambiguous_route_question(question, replies):
    routes = route_catalog()
    requested_runways = set(re.findall(r"\b34[CL]\b", " ".join([question, *replies]).upper()))
    for fix in dict.fromkeys(name.split()[0] for _, name in routes):
        options = [(runway, name) for runway, name in routes if name.split()[0] == fix]
        if len(options) < 2:
            continue
        prefix = rf"(?<!\w){re.escape(fix)}(?![A-Z])"
        suffix = r"[\s-]*\d{1,2}[A-Z]\b"
        if not re.search(prefix + rf"(?!{suffix})\b", question.upper()):
            continue
        if any(re.search(prefix + suffix, reply.upper()) for reply in replies):
            continue
        if requested_runways and all(sum(runway == wanted for runway, _ in options) == 1
                                     for wanted in requested_runways):
            continue
        return route_question(options)
    return None


def resolve_cases(values, defaults=None):
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise ValueError("Choose one case or compare two cases.")
    return [resolve_case(value, defaults or {}) for value in values]


def resolve_case(value, defaults):
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        raise ValueError("A case has fields this tool cannot calculate.")
    for key in FIELDS[:3]:
        if value.get(key) is not None and (not isinstance(value[key], str) or not value[key].strip()):
            raise ValueError(f"Supply a valid {key}.")

    runway, sid = value.get("runway"), value.get("sid")
    if runway is not None:
        runway = runway.strip().upper()
        if runway not in {"34C", "34L"}:
            raise ValueError(f"Runway {runway} is not covered. Choose 34C or 34L.")
    routes = route_catalog()
    if sid is not None:
        requested = compact(sid)
        matches = [(rw, name) for rw, name in routes if (runway is None or rw == runway)
                   and requested in (compact(name), compact(name.split()[0]))]
        if len(matches) != 1:
            if matches:
                raise ValueError(route_question(matches))
            raise ValueError(f"Route {sid.strip()} is not available for the requested runway. Choose a route in the Scenario panel.")
        runway, sid = matches[0]
    else:
        runway = runway or defaults.get("runway")
        sid = defaults.get("sid")
        if (runway, sid) not in routes:
            raise ValueError(f"Which departure route should I use{f' on {runway}' if runway else ''}?")

    name = value.get("aircraft") or defaults.get("aircraft")
    aircraft = AIRCRAFT_ALIASES.get(compact(name)) if isinstance(name, str) else None
    if aircraft is None:
        raise ValueError("Which supported aircraft should I use? Choose A320, A320neo (A20N), or 777-300ER (B77W).")

    count = value.get("count")
    count = 1 if count is None else count
    if type(count) is not int or not 1 <= count <= 1_000_000:
        raise ValueError("Departure count must be a whole number between 1 and 1,000,000.")
    mass_pct = value.get("mass_pct")
    mass_pct = 80.0 if mass_pct is None else number(mass_pct, "Takeoff mass")
    if not 0 < mass_pct <= 100:
        raise ValueError("Takeoff mass must be a percentage of maximum takeoff weight greater than 0 and at most 100.")
    oew, mtow = aircraft_weights(aircraft)
    if mtow * mass_pct / 100 <= oew:
        raise ValueError(f"{aircraft} takeoff mass must be above its operating empty weight ({100 * oew / mtow:.2f}% of maximum).")
    temperature = value.get("temperature_c")
    if temperature is not None:
        temperature = number(temperature, "Noise temperature")
        if not -10 <= temperature <= 60:
            raise ValueError("Noise temperature must lie between −10 and 60 °C for this calculator.")
    return {"runway": runway, "sid": sid, "aircraft": aircraft, "count": count,
            "mass_pct": mass_pct, "temperature_c": temperature}


def number(value, label):
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number.")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite number.")
    return float(value)

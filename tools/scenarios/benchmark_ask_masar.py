"""Compare Ask Masar interpreters against fixed questions and expected results."""

import argparse
from functools import partial
import json
import math
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter, sleep
from urllib.request import ProxyHandler, Request, build_opener

from sans_ask.cases import EXPLANATIONS
from sans_ask.interpret import GROQ_MODELS, ModelUnavailable, ask_groq, ask_ollama, groq_key, interpret


MODELS = ("qwen3.5:4b", "qwen3.5:2b-q4_K_M", "qwen2.5:1.5b")
PANEL = {"runway": "34C", "sid": "BOMOX 2W", "aircraft": "A320"}


def case(sid="BOMOX 2W", aircraft="A320", count=1, mass=80, temp=None):
    return {"runway": "34L" if sid.endswith("3X") else "34C", "sid": sid, "aircraft": aircraft,
            "count": count, "mass_pct": mass, "temperature_c": temp}


CALCULATIONS = [
    ("Estimate fuel for the selected departure.", [case()]),
    ("How much fuel would 20 A320 departures on ISLAM 2W burn?", [case("ISLAM 2W", count=20)]),
    ("Fuel for one A320neo on DATAP 3X at 77.5% of MTOW.", [case("DATAP 3X", "A20N", mass=77.5)]),
    ("Estimate CO2 for 12 Boeing 777-300ER departures on EGPOB 2W.", [case("EGPOB 2W", "B77W", 12)]),
    ("What is the noise footprint at 42 degrees Celsius for the current route and aircraft?", [case(temp=42)]),
    ("Use runway 34L and route EGREP 3X for 5 B77W takeoffs.", [case("EGREP 3X", "B77W", 5)]),
    ("Simulate one A20N on MIGDA 2W with the noise temperature at -5 C.", [case("MIGDA 2W", "A20N", temp=-5)]),
    ("How much fuel for 8 takeoffs at 82% MTOW on the route I have selected?", [case(count=8, mass=82)]),
    ("Run RIBAM 3X for the current aircraft, with 16 departures at 90% MTOW.", [case("RIBAM 3X", count=16, mass=90)]),
    ("Compare 20 A320 departures on ISLAM 2W at 75% versus 80% of maximum takeoff weight.",
     [case("ISLAM 2W", count=20, mass=75), case("ISLAM 2W", count=20)]),
    ("For the current route and aircraft, compare the noise footprint at 30 and 38 degrees Celsius.",
     [case(temp=30), case(temp=38)]),
    ("Compare A320 and A320neo on DATAP 2W for one departure each.",
     [case("DATAP 2W"), case("DATAP 2W", "A20N")]),
    ("Compare 5 A320 takeoffs on BOMOX 2W against 9 A320 takeoffs on BOMOX 3X.",
     [case(count=5), case("BOMOX 3X", count=9)]),
    ("Compare the noise of a B77W on KAROX 2W and KAROX 3X, both at 38 C.",
     [case("KAROX 2W", "B77W", temp=38), case("KAROX 3X", "B77W", temp=38)]),
    ("For the chosen route and aircraft, compare 4 departures versus 10 departures at 85% MTOW.",
     [case(count=4, mass=85), case(count=10, mass=85)]),
    ("Compare an A320 at 77% MTOW with a B77W at 85% MTOW, both on RIBAM 2W.",
     [case("RIBAM 2W", mass=77), case("RIBAM 2W", "B77W", mass=85)]),
    ("Compare 15 A20N departures at 32.5 vs 38.5 degrees C on MIGDA 3X.",
     [case("MIGDA 3X", "A20N", 15, temp=32.5), case("MIGDA 3X", "A20N", 15, temp=38.5)]),
    ("For EGPOB 3X, compare one A320 at 80% MTOW against the same aircraft at 75% MTOW.",
     [case("EGPOB 3X"), case("EGPOB 3X", mass=75)]),
]
CLARIFICATIONS = [
    ("Compare 20 A320 departures on ISLAM at 75% versus 80% of MTOW.", ("ISLAM 2W", "ISLAM 3X")),
    ("Fuel for a B77W on DATAP.", ("DATAP 2W", "DATAP 3X")),
    ("Show the noise footprint for an A20N using the RIBAM route.", ("RIBAM 2W", "RIBAM 3X")),
    ("Estimate fuel for an A380 on ISLAM 2W.", ()),
    ("Calculate noise for a 737 MAX 8 using BOMOX 2W.", ()),
    ("Estimate fuel with runway 34R on ISLAM 2W.", ()),
    ("Fuel for one A320 on the ZEEMA 9Z route.", ()),
    ("Estimate fuel for 5 A320 departures at 25% of MTOW.", ()),
    ("Estimate the current aircraft noise at 72 degrees Celsius.", ()),
    ("Calculate fuel for 0 departures on ISLAM 3X.", ()),
    ("What is the fuel burn for the entire flight to Riyadh, including cruise and landing?", ()),
    ("Compare noise when a 30-knot crosswind blows from the west versus calm wind.", ()),
]
FOLLOW_UPS = [
    ("Fuel for 20 A320 departures on ISLAM at 75% MTOW.", "Which ISLAM route?", "ISLAM 2W",
     [case("ISLAM 2W", count=20, mass=75)]),
    ("Compare 20 A320 departures on ISLAM at 75% versus 80% of MTOW.", "Which ISLAM route?", "ISLAM 3X",
     [case("ISLAM 3X", count=20, mass=75), case("ISLAM 3X", count=20)]),
    ("Estimate fuel for 12 A380 departures on EGREP 2W.", "Which supported aircraft should I use?", "A320neo",
     [case("EGREP 2W", "A20N", 12)]),
    ("Model noise for one B77W on KAROX at 34 C.", "Which KAROX route?", "KAROX 3X",
     [case("KAROX 3X", "B77W", temp=34)]),
    ("Compare 4 A320 departures on MIGDA 2W at 25% and 85% MTOW.", "Use a valid weight for the first case.",
     "Make the first weight 75%.", [case("MIGDA 2W", count=4, mass=75), case("MIGDA 2W", count=4, mass=85)]),
]
EXPLAIN = [
    ("What does LAmax mean?", "noise"),
    ("Do the residents counts describe individual departures or all departures together?", "noise"),
    ("What portion of the flight is included in the fuel estimate?", "fuel"),
    ("What weather assumptions does Masar make?", "assumptions"),
    ("Which aircraft can Masar simulate?", "supported"),
]


def unload(model):
    data = json.dumps({"model": model, "keep_alive": 0}).encode()
    request = Request("http://127.0.0.1:11434/api/generate", data=data,
                      headers={"Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
        json.load(response)


def benchmark(model):
    hosted = model in GROQ_MODELS
    if hosted:
        os.environ["MASAR_GROQ_MODEL"] = model
    else:
        os.environ["MASAR_OLLAMA_MODEL"] = model
        unload(model)
    request = ask_groq if hosted else ask_ollama
    next_request = 0.0

    def run(question, clarifications=None):
        nonlocal next_request
        if hosted:
            sleep(max(0, next_request - perf_counter()))
        timings = {}
        start = perf_counter()
        next_request = start + 12  # Space requests to stay within the free tier during the suite.
        try:
            result = interpret(question, PANEL, partial(request, record_metrics=timings.update),
                               clarifications=clarifications)
        except ModelUnavailable as exc:
            result = {"kind": "error", "message": str(exc)}
        return result, {"wall_s": round(perf_counter() - start, 3), **timings}

    first, first_time = run(CALCULATIONS[0][0])
    if hosted and first["kind"] == "error":
        raise RuntimeError(f"{model} · {first['message']}")
    records = []

    def score(question, kind, expected, clarifications=None):
        result, timing = run(question, clarifications)
        correct = result.get("kind") == kind
        if correct and kind == "calculate":
            correct = result["cases"] == expected
        if correct and kind == "explain":
            correct = result["message"] == EXPLANATIONS[expected]
        if correct and kind == "clarify":
            correct = all(part in result["message"] for part in expected)
        records.append({"question": question, "kind": kind, "correct": correct, **timing,
                        "answer": result, "expected": expected})
        print(f"{model}  {len(records):2d}/40  {timing['wall_s']:.2f}s  {'pass' if correct else 'FAIL'}",
              file=sys.stderr, flush=True)

    for question, expected in CALCULATIONS:
        score(question, "calculate", expected)
    for question, choices in CLARIFICATIONS:
        score(question, "clarify", choices)
    for question, prompt, reply, cases in FOLLOW_UPS:
        score(question, "calculate", cases, [{"prompt": prompt, "reply": reply}])
    for question, topic in EXPLAIN:
        score(question, "explain", topic)
    wall_times = sorted(item["wall_s"] for item in records)
    return {"provider": "groq" if hosted else "ollama",
            "first_request" if hosted else "cold": first_time, "warm_median_s": median(wall_times),
            "warm_p95_s": wall_times[math.ceil(len(wall_times) * .95) - 1],
            "correct": sum(item["correct"] for item in records),
            "clarifications_correct": sum(item["correct"] for item in records if item["kind"] == "clarify"),
            "records": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Write timings and expected/actual answers here")
    parser.add_argument("--models", nargs="+", default=MODELS, choices=(*MODELS, *GROQ_MODELS))
    args = parser.parse_args()
    if any(model in GROQ_MODELS for model in args.models) and not groq_key():
        parser.error("Set GROQ_API_KEY before benchmarking Groq models.")
    results = {}
    for model in args.models:
        try:
            results[model] = benchmark(model)
        except RuntimeError as exc:
            parser.exit(1, str(exc) + "\n")
        if model not in GROQ_MODELS:
            unload(model)
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(model, {key: value for key, value in results[model].items() if key != "records"}, flush=True)


if __name__ == "__main__":
    main()

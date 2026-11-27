from functools import partial
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from sans_a2 import server as local_server
from sans_ask.cases import resolve_cases
from sans_ask import interpret as interpreter
from sans_ask.interpret import interpret
from sans_ask.jobs import BusyError, Jobs, read_progress


DEFAULTS = {"runway": "34C", "sid": "BOMOX 2W", "aircraft": "A320"}
CASE = {"runway": "34C", "sid": "ISLAM 2W", "aircraft": "A320", "count": 20}


def test_groq_sends_a_strict_proposal_request_and_locally_validates_cases(monkeypatch):
    monkeypatch.setenv("MASAR_LLM_PROVIDER", "groq")
    monkeypatch.delenv("MASAR_GROQ_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "demo-test-key")
    proposed = [{**CASE, "mass_pct": mass, "temperature_c": None} for mass in (75, 80)]

    def post(request, timeout):
        body = json.loads(request.data)
        assert request.full_url == "https://api.groq.com/openai/v1/chat/completions"
        assert request.get_header("Authorization") == "Bearer demo-test-key"
        assert request.get_header("User-agent") == "Ask-Masar/1.0"
        assert "demo-test-key" not in request.data.decode()
        assert body["model"] == "openai/gpt-oss-20b" and body["reasoning_effort"] == "low"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert timeout == 15
        return BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "kind": "calculate", "cases": proposed, "topic": None, "question": None,
        })}}]}).encode())

    monkeypatch.setattr(interpreter, "urlopen", post)
    question = "Compare 20 A320 departures on ISLAM 2W at 75% versus 80% of MTOW."
    assert interpret(question, DEFAULTS) == {"kind": "calculate", "cases": proposed}
    proposed[0]["mass_pct"] = 25
    assert interpret(question, DEFAULTS)["kind"] == "clarify"


@pytest.mark.parametrize("code, message", [(401, "rejected the API key"), (403, "denied access"),
                                           (429, "rate limit"), (502, "could not process")])
def test_groq_errors_are_actionable_without_exposing_provider_details(monkeypatch, code, message):
    monkeypatch.setenv("MASAR_LLM_PROVIDER", "groq")
    monkeypatch.delenv("MASAR_GROQ_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "demo-test-key")

    def unavailable(request, timeout):
        raise HTTPError(request.full_url, code, "internal provider error", {}, BytesIO(b"raw provider data"))

    monkeypatch.setattr(interpreter, "urlopen", unavailable)
    with pytest.raises(interpreter.ModelUnavailable, match=message) as error:
        interpret("Calculate fuel", DEFAULTS)
    assert "raw provider data" not in str(error.value)
    assert "demo-test-key" not in str(error.value)


def test_missing_groq_key_disables_only_free_text(monkeypatch):
    monkeypatch.setenv("MASAR_LLM_PROVIDER", "groq")
    monkeypatch.delenv("MASAR_GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert interpreter.model_status()["configured"] is False
    with pytest.raises(interpreter.ModelUnavailable, match="Set GROQ_API_KEY"):
        interpret("Calculate fuel", DEFAULTS)
    assert resolve_cases([CASE])[0]["count"] == 20


def test_interpret_compares_resolved_cases_and_preserves_shared_inputs():
    def parse(messages):
        assert "34L: ISLAM 3X" in messages[0]["content"]
        assert "75% versus 80%" in messages[1]["content"]
        return {"kind": "calculate", "cases": [
            {"sid": "Islam2W", "aircraft": "A320", "count": 20, "mass_pct": 75},
            {"sid": "ISLAM 2W", "aircraft": "A320", "count": 20, "mass_pct": 80},
        ]}

    answer = interpret("20 A320 departures on ISLAM 2W, at 75% versus 80% of MTOW", DEFAULTS, parse)
    assert answer == {"kind": "calculate", "cases": [
        {**CASE, "mass_pct": 75, "temperature_c": None},
        {**CASE, "mass_pct": 80, "temperature_c": None},
    ]}
    assert resolve_cases([{}], DEFAULTS) == [{**DEFAULTS, "count": 1, "mass_pct": 80, "temperature_c": None}]


def test_interpret_asks_about_ambiguous_route_and_unsupported_aircraft():
    parse = lambda case: lambda messages: {"kind": "calculate", "cases": [case]}
    result = interpret("Use ISLAM", DEFAULTS, parse({"sid": "ISLAM"}))
    assert result["kind"] == "clarify"
    assert "ISLAM 2W on 34C" in result["message"] and "ISLAM 3X on 34L" in result["message"]
    assert interpret("Fuel for ISLAM", DEFAULTS, parse({"sid": "ISLAM 2W"})) == result
    assert interpret("Fuel for ISLAM", DEFAULTS, lambda _: {
        "kind": "clarify", "question": "Which route?",
    }) == result
    assert interpret("Fuel for ISLAM on runway 34C", DEFAULTS, parse({"sid": "ISLAM 2W"}))["kind"] == "calculate"
    result = interpret("Use an A380", DEFAULTS, parse({"aircraft": "A380"}))
    assert result["kind"] == "clarify"
    assert "supported aircraft" in result["message"]


def test_explanation_uses_project_text_instead_of_model_claims():
    result = interpret("What is LAmax?", {}, lambda _: {
        "kind": "explain", "topic": "noise", "question": "Estimated noise is 500 dB",
    })
    assert result["kind"] == "explain"
    assert "peak sound level" in result["message"]
    assert "500" not in result["message"]


def test_follow_up_keeps_the_original_plan():
    def parse(messages):
        assert messages[1]["content"].endswith("Fuel for 20 A320 departures on ISLAM at 75% of MTOW")
        assert messages[2:] == [
            {"role": "assistant", "content": "Which ISLAM route?"},
            {"role": "user", "content": "ISLAM 2W"},
        ]
        return {"kind": "calculate", "cases": [{**CASE, "mass_pct": 75}]}

    result = interpret("Fuel for 20 A320 departures on ISLAM at 75% of MTOW", DEFAULTS, parse,
                       clarifications=[{"prompt": "Which ISLAM route?", "reply": "ISLAM 2W"}])
    assert result == {"kind": "calculate", "cases": [{**CASE, "mass_pct": 75, "temperature_c": None}]}
    with pytest.raises(ValueError, match="400 characters"):
        interpret("How much fuel?", DEFAULTS, parse, clarifications=[{"reply": "ISLAM 2W"}])


@pytest.mark.parametrize("change,error", [
    ({"mass_pct": 34}, "operating empty weight"),
    ({"mass_pct": True}, "finite number"),
    ({"temperature_c": 500}, "temperature"),
    ({"count": 1.4}, "whole number"),
    ({"runway": "34R"}, "not covered"),
    ({"runway": "34L"}, "not available"),
    ({"path": "arbitrary"}, "cannot calculate"),
])
def test_rejects_invalid_physics_and_unsupported_case_fields(change, error):
    with pytest.raises(ValueError, match=error):
        resolve_cases([{**CASE, **change}])


def test_only_one_calculation_runs_and_failure_is_reported(monkeypatch):
    started, release, completed = Event(), Event(), Event()

    def calculate(cases, report):
        report("fuel", case=1)
        started.set()
        assert release.wait(5)
        report("noise", case=1)
        raise RuntimeError("The synthetic test calculation failed.")

    jobs = Jobs(calculate)
    finish = jobs._finish

    def finish_and_notify(job, cases):
        try:
            finish(job, cases)
        finally:
            completed.set()

    monkeypatch.setattr(jobs, "_finish", finish_and_notify)
    submitted = jobs.submit([CASE])
    assert started.wait(5)
    try:
        running = jobs.get(submitted["id"])
        assert running["status"] == "running"
        assert running["progress"][-1] == {"stage": "fuel", "case": 1}
        assert running["cases_total"] == 1
        assert running["elapsed_s"] >= 0
        assert submitted["progress"] == [{"stage": "preparing"}]
        with pytest.raises(BusyError):
            jobs.submit([CASE])
    finally:
        release.set()
    assert completed.wait(5)
    failed = jobs.get(submitted["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "The synthetic test calculation failed."
    assert failed["progress"][-1] == {"stage": "noise", "case": 1}
    assert failed["elapsed_s"] >= running["elapsed_s"]


def test_progress_reader_leaves_an_incomplete_event_for_the_next_poll():
    read = BytesIO(b'{"stage":"fuel","case":1}\n{"stage":"noise"')
    events = []

    def report(**event):
        events.append(event)

    read_progress(read, report)
    assert events == [{"stage": "fuel", "case": 1}]
    pending = read.tell()
    read.seek(0, 2)
    read.write(b',"case":1}\n')
    read.seek(pending)
    read_progress(read, report)
    assert events[-1] == {"stage": "noise", "case": 1}


def test_local_http_validates_guided_inputs_and_revalidates_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(local_server, "interpret", partial(interpret, parse=lambda _: {
        "kind": "calculate", "cases": [CASE],
    }))
    with ThreadingHTTPServer(("127.0.0.1", 0), local_server.handler_for({"output_dir": str(tmp_path)})) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/ask-masar"

        def send(path, data=None, origin=None):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            req = Request(base + path, data=json.dumps(data).encode() if data else None, headers=headers)
            with urlopen(req, timeout=5) as response:
                return json.load(response)

        try:
            assert send("/status")["enabled"] is True
            assert send("/interpret", {"question": "Calculate fuel", "defaults": DEFAULTS})["cases"][0]["count"] == 20
            guided = [{**CASE, "mass_pct": 75}, {**CASE, "mass_pct": 80}]
            assert send("/validate", {"cases": guided}) == {
                "cases": [{**case, "temperature_c": None} for case in guided],
            }
            with pytest.raises(HTTPError) as error:
                send("/validate", {"cases": [{**CASE, "mass_pct": 1}]})
            assert error.value.code == 400
            assert "operating empty weight" in json.load(error.value)["error"]
            with pytest.raises(HTTPError) as error:
                send("/jobs", {"cases": [{**CASE, "count": -1}]})
            assert error.value.code == 400
            with pytest.raises(HTTPError) as error:
                send("/interpret", {"question": "Calculate fuel"}, origin="http://foreign.invalid")
            assert error.value.code == 403
        finally:
            server.shutdown()
            thread.join(5)

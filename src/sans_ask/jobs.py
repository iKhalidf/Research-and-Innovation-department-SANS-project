"""Run one isolated calculation at a time and expose its status to Map 2."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import monotonic
from uuid import uuid4

from .cases import ROOT, resolve_cases


class BusyError(RuntimeError):
    pass


def read_progress(log, report):
    while True:
        offset = log.tell()
        line = log.readline()
        if not line.endswith(b"\n"):
            log.seek(offset)
            return
        report(**json.loads(line))


def calculate(cases, report):
    with TemporaryDirectory(prefix="masar-ask-") as path:
        work = Path(path)
        (work / "request.json").write_text(json.dumps(cases), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1", "OMP_NUM_THREADS": "2"}
        env.pop("GROQ_API_KEY", None)
        command = [sys.executable, "-m", "sans_ask.worker", path]
        progress_path, error_path = work / "progress.jsonl", work / "stderr.log"
        progress_path.touch()
        with progress_path.open("rb") as progress_log, error_path.open("wb") as stderr:
            with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=stderr,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                deadline = monotonic() + 900
                try:
                    while process.poll() is None:
                        read_progress(progress_log, report)
                        remaining = deadline - monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, 900)
                        try:
                            process.wait(timeout=min(0.25, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    read_progress(progress_log, report)
        if process.returncode:
            stderr_text = error_path.read_text(encoding="utf-8", errors="replace")
            print(stderr_text.strip(), file=sys.stderr, flush=True)
            error = next((line for line in stderr_text.splitlines()
                          if line.startswith("Ask Masar calculation failed:")), "")
            raise RuntimeError(error or "The simulation failed. See the local server log.")
        return json.loads((work / "response.json").read_text(encoding="utf-8"))["result"]


class Jobs:
    def __init__(self, run=calculate):
        self.run = run
        self.lock = Lock()
        self.active = False
        self.entries = {}

    def submit(self, values):
        cases = resolve_cases(values)
        with self.lock:
            if self.active:
                raise BusyError("A simulation is already running. Try again after it finishes.")
            if len(self.entries) == 8:
                self.entries.pop(next(iter(self.entries)))
            job = uuid4().hex
            state = {"id": job, "status": "running", "cases_total": len(cases),
                     "progress": [{"stage": "preparing"}], "started": monotonic()}
            self.entries[job] = state
            self.active = True
            initial = self._snapshot(state)
        Thread(target=self._finish, args=(job, cases), daemon=True).start()
        return initial

    @staticmethod
    def _snapshot(state):
        public = {key: value for key, value in state.items() if key != "started"}
        public["progress"] = list(state["progress"])
        public["elapsed_s"] = state.get("elapsed_s", round(monotonic() - state["started"], 1))
        return public

    def get(self, job):
        with self.lock:
            state = self.entries.get(job)
            return self._snapshot(state) if state is not None else None

    def _finish(self, job, cases):
        def report(stage, **details):
            with self.lock:
                self.entries[job]["progress"].append({"stage": stage, **details})

        try:
            outcome = {"status": "complete", "result": self.run(cases, report)}
        except subprocess.TimeoutExpired:
            outcome = {"status": "failed", "error": "The simulation exceeded its 15-minute limit."}
        except RuntimeError as exc:
            outcome = {"status": "failed", "error": str(exc)}
        except Exception as exc:
            print(f"Ask Masar worker failed: {exc}", file=sys.stderr, flush=True)
            outcome = {"status": "failed", "error": "The simulation could not finish. See the local server log."}
        with self.lock:
            state = self.entries[job]
            state.update(outcome, elapsed_s=round(monotonic() - state["started"], 1))
            self.active = False

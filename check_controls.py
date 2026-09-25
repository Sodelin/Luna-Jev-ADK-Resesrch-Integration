"""Offline control checks; does not launch Lean or any model."""
import json, tempfile
from pathlib import Path
from unittest.mock import patch
import runner

with tempfile.TemporaryDirectory(prefix="jev-controls-") as directory:
    scratch=Path(directory)
    with patch.object(runner,"ROOT",scratch):
        with runner.lock():
            try:
                with runner.lock(): raise AssertionError("Concurrent lock unexpectedly allowed")
            except SystemExit: pass
        assert not (scratch/"runner.lock").exists()
        with patch.object(runner,"state_now",return_value={"checks_used":runner.LIMIT}), patch.object(runner.subprocess,"run",side_effect=AssertionError("Must not compile")):
            try: runner.check("", "budget-fixture", [])
            except SystemExit as e: assert "limit" in str(e)
            else: raise AssertionError("Exhausted budget accepted")
        (scratch/"STOP").write_text("")
        with patch.object(runner,"state_now",return_value={"checks_used":0}), patch.object(runner.subprocess,"run",side_effect=AssertionError("Must not compile")):
            try: runner.check("", "stop-fixture", [])
            except SystemExit as e: assert "Paused" in str(e)
            else: raise AssertionError("STOP ignored")
result=dict(concurrent_runner_rejected=True,exhausted_budget_rejected=True,stop_prevents_compile=True,lean_checks_consumed=0)
(runner.ROOT/"control-results.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(result))

"""Budget, crash and artifact-integrity tests. No model or Lean subprocess calls."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import luna_loop as loop
import runner


class WorkerControls(unittest.TestCase):
    def setUp(self):
        (loop.ROOT / "loop-runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="controls-", dir=loop.ROOT / "loop-runs")
        self.root = Path(self.temp.name)
        for name in ("manifest.json", "accepted.json", "loop-authorization.json"):
            shutil.copyfile(loop.ROOT / ("loop-authorization.example.json" if name == "loop-authorization.json" else name), self.root / name)
        shutil.copytree(loop.ROOT / "snapshots", self.root / "snapshots")
        runner.writej(self.root / "state.json", {"checks_used": 10, "check_limit": 20})
        self.patches = [patch.object(loop, "ROOT", self.root), patch.object(runner, "ROOT", self.root)]
        for p in self.patches:
            p.start()
        self.store = loop.Store(self.root)
        self.provider = Mock(return_value={"proof": "by\n  exact \u27e80, iterate_zero f one\u27e9"})
        self.worker = loop.Worker(self.store, self.provider)

    def tearDown(self):
        self.store.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def enqueue(self, mode="codex"):
        self.worker.enqueue("test", "reaches_self", mode)

    def authorize(self, limit=1):
        runner.writej(self.root / "loop-authorization.json",
                      {"additional_luna_calls": limit, "approved_by": "test_fixture", "approved_utc": runner.now()})

    def test_exhausted_allowance_never_calls_provider(self):
        self.enqueue()
        with self.assertRaisesRegex(RuntimeError, "allowance spent"):
            self.worker.propose("test")
        self.provider.assert_not_called()
        self.assertEqual(self.store.live_used(), 0)

    def test_failed_provider_reservation_survives_restart(self):
        self.authorize()
        self.enqueue()
        self.provider.side_effect = TimeoutError("simulated ambiguous completion")
        with self.assertRaises(TimeoutError):
            self.worker.propose("test")
        self.store.close()
        self.store = loop.Store(self.root)
        self.worker = loop.Worker(self.store, self.provider)
        self.assertEqual(self.store.live_used(), 1)
        self.assertEqual(self.worker.run("test")["phase"], "provider_error")
        self.assertEqual(self.provider.call_count, 1)
        self.worker.enqueue("another", "reaches_self", "codex")
        with self.assertRaisesRegex(RuntimeError, "allowance spent"):
            self.worker.propose("another")
        self.assertEqual(self.provider.call_count, 1)

    def test_interrupted_reservation_stops_without_replay(self):
        self.authorize()
        self.enqueue()
        job = self.store.get("test")
        self.store.reserve(job, loop.packet_for(job))
        self.assertEqual(self.worker.run("test")["phase"], "needs_review")
        self.provider.assert_not_called()
        self.assertEqual(self.store.live_used(), 1)

    def test_stop_and_global_lean_limit_do_not_spend_luna(self):
        self.authorize()
        self.enqueue()
        stop = self.root / "STOP"
        stop.touch()
        with self.assertRaisesRegex(RuntimeError, "STOP"):
            self.worker.propose("test")
        stop.unlink()
        runner.writej(self.root / "state.json", {"checks_used": 20, "check_limit": 20})
        with self.assertRaisesRegex(RuntimeError, "Lean budget"):
            self.worker.propose("test")
        self.provider.assert_not_called()
        self.assertEqual(self.store.live_used(), 0)

    def test_snapshot_change_and_job_redefinition_rejected(self):
        self.enqueue("replay")
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.worker.enqueue("test", "iterate_commute", "codex")
        path = self.root / "snapshots/RankedCoalescenceSound.lean"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.worker.propose("test")
        self.provider.assert_not_called()

    def test_unsafe_proof_never_reaches_lean(self):
        self.enqueue("replay")
        self.provider.return_value = {"proof": "by\n  sorry"}
        with patch.object(runner, "check") as verifier:
            with self.assertRaisesRegex(ValueError, "Proof rejected"):
                self.worker.propose("test")
            verifier.assert_not_called()
        self.assertEqual(self.worker.status("test")["phase"], "provider_error")

    def test_modified_candidate_never_reaches_lean(self):
        self.enqueue("replay")
        self.worker.propose("test")
        job = self.store.get("test")
        job["proof"] = "by\n  exact False.elim"
        self.store.save(job)
        with patch.object(runner, "check") as verifier:
            with self.assertRaisesRegex(ValueError, "Candidate changed"):
                self.worker.verify("test")
            verifier.assert_not_called()

    def test_atomic_reservation_prevents_overspend(self):
        self.authorize()
        self.enqueue()
        second = loop.Store(self.root)
        try:
            first = self.store.get("test")
            self.store.reserve(first, loop.packet_for(first))
            self.worker.enqueue("next", "reaches_self", "codex")
            with self.assertRaisesRegex(RuntimeError, "allowance spent"):
                second.reserve(second.get("next"), {})
            self.assertEqual(second.live_used(), 1)
        finally:
            second.close()

    def test_process_lock_excludes_second_holder(self):
        with loop.process_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "owns"):
                with loop.process_lock(self.root):
                    pass

    def test_completed_resume_checks_artifact_without_recompiling(self):
        self.enqueue("replay")
        job = self.store.get("test")
        artifact = self.root / "accepted-test.lean"
        source = "theorem fixture : True := by\n  trivial\n"
        artifact.write_text(source, encoding="utf-8", newline="\n")
        digest = runner.digest(source.encode("utf-8"))
        receipt = self.root / "accepted-test.result.json"
        runner.writej(receipt, {"status": "accepted", "source_sha256": digest})
        job.update(phase="accepted", accepted_file=artifact.name,
                   receipt_file=receipt.name, accepted_source_sha256=digest)
        self.store.save(job)
        with patch.object(runner, "check") as verifier:
            self.assertEqual(self.worker.run("test")["phase"], "accepted")
            artifact.write_text(source + "\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "artifact or Lean receipt changed"):
                self.worker.run("test")
            verifier.assert_not_called()
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

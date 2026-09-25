"""Isolated queue-contract tests; mock providers and compiler, no external calls."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import corollary_queue as cq
import luna_loop as loop
import runner


class CorollaryControls(unittest.TestCase):
    def setUp(self):
        self.parent = loop.ROOT / "loop-runs"
        self.parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="corollary-controls-", dir=self.parent)
        self.root = Path(self.temp.name).resolve()
        (self.root / "attempts").mkdir()
        self.input = self.root / "intake"
        self.input.mkdir()
        self.compiler = self.root / "fixture-lean.exe"
        self.compiler.write_bytes(b"not executable; all compiler calls are mocked")
        runner.writej(self.root / "state.json", {"checks_used": 12, "check_limit": 20})
        runner.writej(self.root / "loop-authorization.json", {"additional_luna_calls": 0})
        self.events = []
        self.provider = Mock(side_effect=self.provide)
        self.compiler_mock = Mock(side_effect=self.compile)
        self.patches = [patch.object(loop, "ROOT", self.root), patch.object(runner, "ROOT", self.root),
                        patch.object(runner, "LEAN", self.compiler), patch.object(runner.subprocess, "run", self.compiler_mock)]
        for p in self.patches:
            p.start()
        self.store = loop.Store(self.root)
        self.queue = cq.Queue(self.store, self.provider)
        now = datetime.now(timezone.utc)
        self.packet = {"schema_version": 1, "queue_id": "three-goals", "targets": []}
        for i in range(3):
            source = self.input / ("source-" + str(i) + ".lean")
            source.write_text("-- Frozen offline test fixture\n", encoding="utf-8")
            self.packet["targets"].append({
                "id": "goal-" + str(i), "endpoint": "queue_goal_" + str(i), "statement": ": True",
                "source": {"path": source.name, "sha256": runner.digest(source.read_bytes())}, "dependencies": [],
                "origin": {"repository": "offline-test-fixture", "revision": "1" * 40},
                "ownership": {"status": "unowned", "already_proven": False, "coordinator": "offline test",
                              "evidence": "synthetic targets, not research obligations", "confirmed_utc": (now-timedelta(seconds=10)).isoformat(),
                              "valid_until_utc": (now+timedelta(hours=1)).isoformat()},
                "reviewed_by": "offline test fixture"})
        self.manifest = self.input / "packet.json"
        self.save_packet()

    def tearDown(self):
        self.store.close()
        for p in reversed(self.patches):
            p.stop()
        # Explicit containment before the test-owned temporary tree is removed.
        self.assertTrue(self.root.is_relative_to(self.parent.resolve()))
        self.assertTrue(self.root.name.startswith("corollary-controls-"))
        self.temp.cleanup()

    def save_packet(self):
        runner.writej(self.manifest, self.packet)

    def prepare(self):
        return self.queue.prepare(self.manifest)

    def authorize(self, provider_cap=3, lean_cap=3, attempts=1, global_cap=3):
        record = self.queue.get("three-goals")
        now = datetime.now(timezone.utc)
        grant = {"packet_sha256": record["packet_sha256"], "max_provider_calls": provider_cap,
                 "max_lean_checks": lean_cap, "max_attempts_per_job": attempts,
                 "deadline_utc": (now+timedelta(seconds=900)).isoformat(), "lease_id": "fixture-lease"}
        runner.writej(self.root / "loop-authorization.json", {"additional_luna_calls": global_cap,
                      "approved_by": "offline test only", "approved_utc": now.isoformat(), "corollary_queues": {"three-goals": grant}})
        runner.writej(self.root / "corollary-lease.local.json", {
            "lease_id": "fixture-lease", "queue_id": "three-goals", "packet_sha256": record["packet_sha256"],
            "host": cq.host(), "root": str(self.root), "holder": "corollary-queue", "granted_by": "offline fixture",
            "resources": ["luna_provider", "lean_compiler"], "issued_utc": (now-timedelta(seconds=1)).isoformat(),
            "expires_utc": (now+timedelta(seconds=850)).isoformat(), "lean_sha256": runner.digest(self.compiler.read_bytes())})

    def provide(self, packet, job, directory):
        self.events.append("provider:" + job["target_id"])
        return {"proof": "by\n  exact True.intro", "provider": "offline_mock", "cloud_invocations": 0}

    def compile(self, argv, **kwargs):
        source = Path(argv[1]).read_text(encoding="utf-8")
        endpoints = re.findall(r"^#print axioms ([\w.]+)$", source, re.M)
        self.events.append("compiler:" + endpoints[-1])
        return SimpleNamespace(returncode=0, stdout="\n".join("'"+n+"' does not depend on any axioms" for n in endpoints), stderr="")

    def assert_no_execution(self):
        self.provider.assert_not_called()
        self.compiler_mock.assert_not_called()
        self.assertEqual(runner.state_now()["checks_used"], 12)

    def test_prepare_is_idempotent_without_spending_or_second_budget(self):
        a = self.prepare()
        self.assertEqual(a, self.prepare())
        self.assertEqual(len(a["jobs"]), 3)
        self.assertEqual(self.store.live_used(), 0)
        self.assertEqual(list(self.root.glob("*.sqlite3")), [self.root / "loop-state.sqlite3"])
        self.assert_no_execution()

    def test_fourth_or_owned_or_published_target_rejected(self):
        self.packet["targets"].append(dict(self.packet["targets"][0]))
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "one to three"):
            self.prepare()
        self.packet["targets"].pop()
        for field, value in (("status", "owned"), ("already_proven", True)):
            original = self.packet["targets"][0]["ownership"][field]
            self.packet["targets"][0]["ownership"][field] = value
            self.save_packet()
            with self.assertRaisesRegex(ValueError, "unowned, unproved"):
                self.prepare()
            self.packet["targets"][0]["ownership"][field] = original
        self.assert_no_execution()

    def test_exact_source_statement_and_dependency_identity(self):
        dep = self.input / "dependency.lean"
        dep.write_text("theorem queue_dep : True := by\n  exact True.intro\n", encoding="utf-8")
        self.packet["targets"][0]["dependencies"] = [{"source": {"path": dep.name, "sha256": runner.digest(dep.read_bytes())}, "endpoints": ["queue_dep"]}]
        self.save_packet()
        self.prepare()
        self.packet["targets"][0]["statement"] = ": False"
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.prepare()
        frozen = self.root / "corollary-inputs/three-goals/dependency.lean"
        frozen.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.queue.dispatch("three-goals")
        self.assert_no_execution()

    def test_path_escape_and_nonstandard_imports_rejected(self):
        self.packet["targets"][0]["source"]["path"] = "../fixture-lean.exe"
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.prepare()
        path = self.input / "source-0.lean"
        path.write_text("import Mathlib\n", encoding="utf-8")
        self.packet["targets"][0]["source"] = {"path": path.name, "sha256": runner.digest(path.read_bytes())}
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "standalone packet"):
            self.prepare()
        self.assert_no_execution()

    def test_duplicate_goal_cannot_create_a_new_queue(self):
        self.prepare()
        self.packet["queue_id"] = "renamed-queue"
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "already admitted"):
            self.prepare()
        self.assert_no_execution()

    def test_no_new_authorization_means_no_execution(self):
        self.prepare()
        with self.assertRaisesRegex(RuntimeError, "authorization"):
            self.queue.dispatch("three-goals")
        self.assert_no_execution()

    def test_missing_wrong_host_and_expired_lease_fail_closed(self):
        self.prepare()
        self.authorize()
        path = self.root / "corollary-lease.local.json"
        good = runner.readj(path, {})
        for changes in ({}, {**good, "host": "some-other-computer"}, {**good, "expires_utc": "2000-01-01T00:00:00Z"}):
            runner.writej(path, changes)
            with self.assertRaises((ValueError, RuntimeError)):
                self.queue.dispatch("three-goals")
        self.assert_no_execution()

    def test_shared_legacy_process_and_compiler_locks_exclude_dispatch(self):
        self.prepare()
        self.authorize()
        with loop.process_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "owns"):
                self.queue.dispatch("three-goals")
        with runner.lock():
            with self.assertRaisesRegex(SystemExit, "Another runner"):
                self.queue.dispatch("three-goals")
        self.assert_no_execution()

    def test_three_jobs_are_serial_and_resume_makes_zero_calls(self):
        self.prepare()
        self.authorize()
        result = self.queue.dispatch("three-goals")
        self.assertTrue(all(j["phase"] == "accepted" for j in result["jobs"]))
        self.assertEqual(self.events, ["provider:goal-0", "compiler:queue_goal_0", "provider:goal-1", "compiler:queue_goal_1", "provider:goal-2", "compiler:queue_goal_2"])
        self.assertEqual(self.store.live_used(), 3)
        self.assertEqual(runner.state_now()["checks_used"], 15)
        # Completed receipt inspection does not need a fresh execution lease.
        (self.root / "corollary-lease.local.json").unlink()
        self.assertEqual(self.queue.dispatch("three-goals"), result)
        self.assertEqual(self.provider.call_count, 3)
        self.assertEqual(self.compiler_mock.call_count, 3)

    def test_original_provider_allowance_is_shared_with_legacy_jobs(self):
        self.prepare()
        self.authorize()
        for i in range(2):
            job = {"id": "legacy-" + str(i), "mode": "codex", "attempt": 0, "phase": "ready"}
            self.store.save(job)
            self.store.reserve(job, {})
        with self.assertRaisesRegex(RuntimeError, "allowance spent"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.store.live_used(), 3)
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(self.compiler_mock.call_count, 1)

    def test_global_lean_and_queue_caps_stop_further_spending(self):
        self.prepare()
        self.authorize(lean_cap=1)
        with self.assertRaisesRegex(RuntimeError, "Lean cap"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(self.compiler_mock.call_count, 1)
        runner.writej(self.root / "state.json", {"checks_used": 20, "check_limit": 20})
        self.authorize()
        with self.assertRaisesRegex(RuntimeError, "Lean cap"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.provider.call_count, 1)

    def test_stop_and_stale_ownership_do_not_spend(self):
        self.prepare()
        self.authorize()
        stop = self.root / "STOP"
        stop.touch()
        with self.assertRaisesRegex(RuntimeError, "STOP"):
            self.queue.dispatch("three-goals")
        stop.unlink()
        # Do not edit frozen evidence to refresh it: a new admission would be needed.
        with patch.object(cq, "datetime") as clock:
            clock.fromisoformat.side_effect = datetime.fromisoformat
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(days=2)
            with self.assertRaisesRegex(ValueError, "not current"):
                self.queue.dispatch("three-goals")
        self.assert_no_execution()

    def short_clearance(self, indices, remaining_seconds):
        now = datetime.now(timezone.utc)
        for index in indices:
            owner = self.packet["targets"][index]["ownership"]
            owner["confirmed_utc"] = (now-timedelta(seconds=1)).isoformat()
            owner["valid_until_utc"] = (now+timedelta(seconds=remaining_seconds)).isoformat()
        self.save_packet()
        self.prepare()
        self.authorize()
        return now

    def test_near_expiry_clearance_blocks_before_provider_reservation(self):
        now = self.short_clearance(range(3), 5)
        with patch.object(cq, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            def response_after_expiry(packet, job, directory):
                clock.now.return_value = now + timedelta(seconds=6)
                return {"proof": "by\n  exact True.intro", "provider": "offline_mock"}
            self.provider.side_effect = response_after_expiry
            with self.assertRaisesRegex(RuntimeError, "Ownership clearance has insufficient time"):
                self.queue.dispatch("three-goals")
        self.assertEqual(self.store.live_used(), 0)
        self.assertTrue(all(j["phase"] == "ready" for j in self.queue.status("three-goals")["jobs"]))
        self.assert_no_execution()

    def test_later_sibling_clearance_is_checked_before_first_proposal(self):
        self.short_clearance([2], 5)
        with self.assertRaisesRegex(RuntimeError, "Ownership clearance has insufficient time"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.store.live_used(), 0)
        self.assert_no_execution()

    def test_proposal_requires_full_provider_and_verifier_margin(self):
        required = loop.CALL_SECONDS + runner.CALL_SECONDS + 20
        self.short_clearance([0], required-1)
        with self.assertRaisesRegex(RuntimeError, "Ownership clearance has insufficient time"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.store.live_used(), 0)
        self.assert_no_execution()

    def test_candidate_resume_checks_ownership_margin_before_lean_reservation(self):
        self.short_clearance([1], runner.CALL_SECONDS+9)
        record = self.queue.get("three-goals")
        job = self.store.get(record["jobs"][0])
        # Simulate an already recorded proposal in the isolated fixture ledger.
        self.store.reserve(job, {})
        proof = "by\n  exact True.intro"
        job.update(phase="candidate", proof=proof, proof_sha256=runner.digest(proof.encode()))
        self.store.proposal_result(job, {"proof": proof, "provider": "offline_fixture"})
        with self.assertRaisesRegex(RuntimeError, "Ownership clearance has insufficient time"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.store.live_used(), 1)
        self.assertEqual(self.queue.status("three-goals")["verification_reservations"], 0)
        self.assert_no_execution()

    def test_provider_failure_stops_queue_and_retains_reservation(self):
        self.prepare()
        self.authorize()
        self.provider.side_effect = TimeoutError("ambiguous provider completion")
        with self.assertRaises(TimeoutError):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.store.live_used(), 1)
        with self.assertRaisesRegex(RuntimeError, "needs review"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.provider.call_count, 1)
        self.compiler_mock.assert_not_called()

    def test_interrupted_reservation_requires_review_before_other_jobs(self):
        self.prepare()
        self.authorize()
        record = self.queue.get("three-goals")
        self.store.reserve(self.store.get(record["jobs"][0]), {})
        with self.assertRaisesRegex(RuntimeError, "needs review"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.queue.status("three-goals")["jobs"][0]["phase"], "needs_review")
        self.assert_no_execution()

    def test_malformed_proof_never_reaches_compiler(self):
        self.prepare()
        self.authorize()
        self.provider.side_effect = None
        self.provider.return_value = {"proof": "by\n  sorry"}
        with self.assertRaisesRegex(ValueError, "candidate policy"):
            self.queue.dispatch("three-goals")
        self.compiler_mock.assert_not_called()

    def test_source_change_after_proposal_prevents_compilation(self):
        self.prepare()
        self.authorize()
        def mutate(packet, job, directory):
            (self.root / "corollary-inputs/three-goals/source-0.lean").write_text("changed", encoding="utf-8")
            return {"proof": "by\n  exact True.intro"}
        self.provider.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.queue.dispatch("three-goals")
        self.compiler_mock.assert_not_called()
        self.assertEqual(self.store.live_used(), 1)

    def test_accepted_receipt_tamper_rejected_without_new_calls(self):
        self.prepare()
        self.authorize()
        self.queue.dispatch("three-goals")
        record = self.queue.get("three-goals")
        job = self.store.get(record["jobs"][0])
        receipt = self.root / job["lean_reservations"][-1]["receipt_file"]
        data = runner.readj(receipt, {})
        data["output"] += "tampered"
        runner.writej(receipt, data)
        with self.assertRaisesRegex(ValueError, "receipt changed"):
            self.queue.dispatch("three-goals")
        self.assertEqual(self.provider.call_count, 3)
        self.assertEqual(self.compiler_mock.call_count, 3)

    def test_rejected_proof_repairs_within_shared_aggregate_caps(self):
        self.prepare()
        self.authorize(provider_cap=4, lean_cap=4, attempts=2, global_cap=4)
        def reject_first(argv, **kwargs):
            if self.compiler_mock.call_count == 1:
                return SimpleNamespace(returncode=1, stdout="fixture type mismatch", stderr="")
            return self.compile(argv, **kwargs)
        self.compiler_mock.side_effect = reject_first
        result = self.queue.dispatch("three-goals")
        self.assertTrue(all(j["phase"] == "accepted" for j in result["jobs"]))
        self.assertEqual([j["attempt"] for j in result["jobs"]], [2, 1, 1])
        self.assertEqual(result["provider_reservations"], 4)
        self.assertEqual(result["verification_reservations"], 4)
        self.assertEqual(runner.state_now()["checks_used"], 16)

    def test_dependency_axiom_failure_cannot_be_accepted(self):
        dep = self.input / "dependency.lean"
        dep.write_text("theorem queue_dep : True := by\n  exact True.intro\n", encoding="utf-8")
        self.packet["targets"][0]["dependencies"] = [{"source": {"path": dep.name, "sha256": runner.digest(dep.read_bytes())}, "endpoints": ["queue_dep"]}]
        self.save_packet()
        self.prepare()
        self.authorize()
        def bad_dependency(argv, **kwargs):
            output = self.compile(argv, **kwargs)
            output.stdout = output.stdout.replace("'queue_dep' does not depend on any axioms", "'queue_dep' depends on axioms: [forbidden_custom_assumption]")
            return output
        self.compiler_mock.side_effect = bad_dependency
        result = self.queue.dispatch("three-goals")
        self.assertEqual(result["jobs"][0]["phase"], "attempt_limit")
        self.assertTrue(all(j["phase"] == "accepted" for j in result["jobs"][1:]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Persistent, bounded proposal/Lean worker. No model calls in replay mode.

The SQLite ledger owns proposal reservations; runner.py owns the original global
Lean budget. ADK can drive these same transitions through adk_loop.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager

import runner

ROOT = Path(__file__).resolve().parent
MODEL = "gpt-6-luna"
MAX_ATTEMPTS, MAX_PROMPT, MAX_PROOF, MAX_ERROR = 3, 8000, 4096, 1800
CALL_SECONDS, JOB_SECONDS = 90, 300
TERMINAL = {"accepted", "attempt_limit", "needs_review", "provider_error"}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def sha(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


@contextmanager
def process_lock(root=ROOT):
    """OS-held lock releases on process death; never silently steal a live lock."""
    with (root / "loop-process.lock").open("a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another Luna-loop process owns the local queue") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, root=ROOT):
        self.root = root
        self.db = sqlite3.connect(root / "loop-state.sqlite3", timeout=5)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, mode TEXT NOT NULL,
                attempt INTEGER NOT NULL, reserved_utc TEXT NOT NULL,
                result TEXT, UNIQUE(job_id, attempt));
        """)

    def close(self):
        self.db.close()

    def get(self, job_id):
        row = self.db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("Unknown job; enqueue it first")
        return json.loads(row[0])

    def save(self, job):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["id"], canonical(job)))

    def live_used(self):
        return self.db.execute("SELECT count(*) FROM proposals WHERE mode='codex'").fetchone()[0]

    def reserve(self, job, packet):
        """Commit spending before invoking any provider, including uncertain failures."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if job["mode"] == "codex":
                auth = runner.readj(self.root / "loop-authorization.json", {})
                limit = auth.get("additional_luna_calls", 0)
                if type(limit) is not int or limit < 0:
                    raise ValueError("Invalid Luna authorization ledger")
                if self.live_used() >= limit or not auth.get("approved_by") or not auth.get("approved_utc"):
                    raise RuntimeError("Luna allowance spent: no additional call is authorized")
            job["attempt"] += 1
            job["phase"] = "proposal_reserved"
            job["prompt_sha256"] = sha(packet)
            job["prompt_chars"] = len(canonical(packet))
            self.db.execute("INSERT INTO proposals(job_id,mode,attempt,reserved_utc) VALUES(?,?,?,?)",
                            (job["id"], job["mode"], job["attempt"], runner.now()))
            self.db.execute("UPDATE jobs SET data=? WHERE id=?", (canonical(job), job["id"]))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def proposal_result(self, job, result):
        with self.db:
            self.db.execute("UPDATE proposals SET result=? WHERE job_id=? AND attempt=?",
                            (canonical(result), job["id"], job["attempt"]))
            self.db.execute("UPDATE jobs SET data=? WHERE id=?", (canonical(job), job["id"]))


def frozen_task(name):
    manifest = runner.readj(ROOT / "manifest.json", {})
    if manifest.get("theorems") != runner.THEOREMS or name not in runner.THEOREMS:
        raise ValueError("Frozen theorem manifest mismatch")
    raw = (ROOT / "snapshots/RankedCoalescenceSound.lean").read_bytes()
    if runner.digest(raw) != manifest["sha256"]:
        raise ValueError("Frozen source hash mismatch")
    accepted = runner.readj(ROOT / "accepted.json", {})
    deps = {n: accepted[n] for n in runner.DEPENDENCIES[name]}
    return {"name": name, "statement": runner.THEOREMS[name],
            "snapshot_sha256": manifest["sha256"], "dependencies": deps}


def packet_for(job):
    """Exact local lookup: a tiny source excerpt and only required helper statements."""
    task = frozen_task(job["theorem"])
    if sha(task) != job["task_sha256"]:
        raise ValueError("Task or dependency changed since enqueue; refusing reuse")
    source = (ROOT / "snapshots/RankedCoalescenceSound.lean").read_text(encoding="utf-8")
    # These complete declarations precede reaches_iterate in the frozen snapshot.
    snippet = source[source.index("def iterate"):source.index("/-- Every tail")]
    packet = {
        "instruction": "Return only JSON with one proof field containing a Lean 4 by block. "
        "Keep the theorem statement unchanged. Use no tools or delegation. No sorry, "
        "admit, new axioms, native_decide, imports, commands or metaprogramming. "
        "The external worker compiles the proof. Keep the proof under 4096 characters.",
        "lean_version": "4.33.1",
        "namespace_open": "NewMathDiscovery.RankedCoalescence",
        "goal": "theorem " + task["name"] + " " + task["statement"],
        "source_excerpt": snippet,
        "available_helpers": {n: runner.THEOREMS[n] for n in task["dependencies"]},
        "last_proof": job.get("proof", ""),
        "last_lean_error": job.get("feedback", "")[:MAX_ERROR],
    }
    if len(canonical(packet)) > MAX_PROMPT:
        raise ValueError("Context packet exceeds the hard character limit")
    return packet


class ReplayProvider:
    """Recorded regression responses, explicitly not fresh model generations."""
    def __call__(self, packet, job, directory):
        if job["theorem"] != "reaches_self":
            raise ValueError("Replay fixture is defined only for reaches_self")
        proof = "by\n  exact \u27e81, iterate_zero f one\u27e9" if job["attempt"] == 1 else \
                "by\n  exact \u27e80, iterate_zero f one\u27e9"
        return {"proof": proof, "provider": "recorded_fixture", "usage": None, "cloud_calls": 0}


def codex_command(directory):
    config = runner.readj(ROOT / "loop-runtime.local.json", {})
    executable = Path(config.get("codex_executable", ""))
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("Configure the verified native codex.exe path in loop-runtime.local.json")
    # A dedicated read-only child keeps managed requirements and execpolicy rules.
    # It skips personal model/plugin defaults for this one invocation only.
    args = [str(executable), "exec", "--ignore-user-config", "--ephemeral", "--json",
            "--sandbox", "read-only", "--skip-git-repo-check", "--model", MODEL,
            "--config", 'model_reasoning_effort="medium"',
            "--config", 'skills.max_context_tokens=1',
            "--config", 'web_search="disabled"',
            "--config", 'approval_policy="never"',
            "--output-schema", str(ROOT / "loop-response.schema.json"),
            "--output-last-message", str(directory / "response.json"),
            "--cd", str(ROOT / "loop-worker-context")]
    for feature in ("multi_agent", "multi_agent_v2", "apps", "browser_use", "browser_use_external",
                    "in_app_browser", "shell_tool", "unified_exec", "code_mode", "code_mode_host"):
        args += ["--disable", feature]
    return args + ["-"]


def stop_child(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        process.kill()
    process.wait(timeout=10)


class CodexProvider:
    def __call__(self, packet, job, directory):
        args = codex_command(directory)
        runner.writej(directory / "invocation.json", {"argv": args, "model": MODEL,
                      "reasoning": "medium", "prompt_chars": len(canonical(packet))})
        output = directory / "codex-events.jsonl"
        errors = directory / "codex-stderr.txt"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        start = time.monotonic()
        with output.open("wb") as out, errors.open("wb") as err:
            child_env = os.environ.copy()
            child_env.pop("OPENAI_API_KEY", None)
            child_env.pop("CODEX_API_KEY", None)
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                       cwd=ROOT, shell=False, creationflags=flags, env=child_env)
            try:
                process.stdin.write(canonical(packet).encode("utf-8"))
                process.stdin.close()
                while process.poll() is None:
                    if (ROOT / "STOP").exists():
                        raise RuntimeError("STOP requested during Luna call; reservation remains spent")
                    if time.monotonic() - start > CALL_SECONDS:
                        raise TimeoutError("Luna call time limit; reservation remains spent")
                    if output.stat().st_size + errors.stat().st_size > 262144:
                        raise RuntimeError("Luna log size limit")
                    time.sleep(0.2)
            finally:
                stop_child(process)
        if process.returncode != 0:
            raise RuntimeError("Codex exited with code " + str(process.returncode) + "; inspect saved stderr")
        events = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
        text_types = {"agent_message", "reasoning", "plan"}
        if any("item" in e and e["item"].get("type") not in text_types for e in events):
            raise RuntimeError("Unexpected tool use in proof-only invocation; result not accepted")
        if any(e.get("type") in ("error", "turn.failed") for e in events):
            raise RuntimeError("Codex reported an error; no automatic retry")
        response = directory / "response.json"
        if not response.is_file() or response.stat().st_size > 16384:
            raise ValueError("Missing or oversized structured proof response")
        payload = runner.readj(response, {})
        if set(payload) != {"proof"}:
            raise ValueError("Structured proof response has unexpected fields")
        usages = [e["usage"] for e in events if e.get("type") == "turn.completed" and e.get("usage")]
        return {"proof": payload["proof"], "provider": "codex_cli", "requested_model": MODEL,
                "usage": usages or None, "elapsed_seconds": round(time.monotonic() - start, 3),
                "cloud_invocations": 1,
                "accounting_note": "CLI invocations are capped; internal inference requests and retries are not separately metered."}


class Worker:
    def __init__(self, store, provider=None):
        self.store = store
        self.provider = provider

    def enqueue(self, job_id, theorem, mode):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", job_id):
            raise ValueError("Job id must be a short lowercase identifier")
        task = frozen_task(theorem)
        try:
            old = self.store.get(job_id)
        except ValueError:
            old = None
        if old:
            if (old["mode"], old["theorem"], old["task_sha256"]) != (mode, theorem, sha(task)):
                raise ValueError("Existing job identity differs; refusing overwrite")
            return self.status(job_id)
        if mode == "replay" and theorem != "reaches_self":
            raise ValueError("Only reaches_self has a recorded repair fixture")
        job = {"id": job_id, "theorem": theorem, "mode": mode, "task_sha256": sha(task),
               "phase": "ready", "attempt": 0, "max_attempts": MAX_ATTEMPTS,
               "created_utc": runner.now(), "history": []}
        self.store.save(job)
        return self.status(job_id)

    def status(self, job_id):
        job = self.store.get(job_id)
        return {k: v for k, v in job.items() if k not in ("proof", "feedback")}

    def preflight(self, job):
        if (ROOT / "STOP").exists():
            raise RuntimeError("Paused by STOP file")
        packet_for(job)  # Recheck source and dependency identity on every transition.
        if runner.state_now()["checks_used"] >= runner.LIMIT:
            raise RuntimeError("Global Lean budget exhausted; do not spend a proposal")

    def propose(self, job_id):
        job = self.store.get(job_id)
        if job["phase"] != "ready":
            return self.status(job_id)
        self.preflight(job)
        if job["attempt"] >= MAX_ATTEMPTS:
            job["phase"] = "attempt_limit"
            self.store.save(job)
            return self.status(job_id)
        packet = packet_for(job)
        self.store.reserve(job, packet)
        directory = ROOT / "loop-runs" / job_id / str(job["attempt"])
        directory.mkdir(parents=True, exist_ok=False)
        runner.writej(directory / "request.json", packet)
        provider = self.provider or (ReplayProvider() if job["mode"] == "replay" else CodexProvider())
        try:
            result = provider(packet, job, directory)
            proof = result.get("proof")
            good, reason = runner.candidate_ok(proof)
            if not isinstance(proof, str) or len(proof) > MAX_PROOF or not good:
                raise ValueError("Proof rejected before Lean: " + (reason or "size limit"))
            job.update(phase="candidate", proof=proof, proof_sha256=runner.digest(proof.encode("utf-8")))
            runner.writej(directory / "provider-result.json", result)
            self.store.proposal_result(job, result)
        except Exception as exc:
            job.update(phase="provider_error", feedback=str(exc))
            self.store.proposal_result(job, {"error": str(exc), "reservation_spent": True})
            raise
        return self.status(job_id)

    def verify(self, job_id):
        job = self.store.get(job_id)
        if job["phase"] != "candidate":
            return self.status(job_id)
        self.preflight(job)
        proof = job["proof"]
        if not runner.candidate_ok(proof)[0] or runner.digest(proof.encode("utf-8")) != job["proof_sha256"]:
            raise ValueError("Candidate changed before verification")
        task = frozen_task(job["theorem"])
        source = runner.module_for(job["theorem"], proof, task["dependencies"])
        endpoints = ["JevPilot." + n for n in [*task["dependencies"], job["theorem"]]]
        with runner.lock():
            job["phase"] = "checking"
            self.store.save(job)
            result = runner.check(source, "loop-" + job_id + "-" + str(job["attempt"]), endpoints)
        job["history"].append({"attempt": job["attempt"], "check_id": result["id"],
                               "status": result["status"], "prompt_chars": job["prompt_chars"]})
        job["feedback"] = result["output"][:MAX_ERROR]
        job["phase"] = "accepted" if result["status"] == "accepted" else \
                       ("attempt_limit" if job["attempt"] >= MAX_ATTEMPTS else "ready")
        if job["phase"] == "accepted":
            dest = ROOT / "loop-runs" / job_id / "Accepted.lean"
            dest.write_text(source, encoding="utf-8", newline="\n")
            job.update(accepted_file=str(dest.relative_to(ROOT)), accepted_source_sha256=runner.digest(source.encode()),
                       receipt_file="attempts/" + result["id"] + ".result.json")
        self.store.save(job)
        return self.status(job_id)

    def check_resume(self, job_id):
        job = self.store.get(job_id)
        if job["phase"] in {"proposal_reserved", "checking"}:
            job.update(phase="needs_review", feedback="Interrupted operation: reconcile its receipt before retrying; reservation remains spent.")
            self.store.save(job)
        if job["phase"] == "accepted":
            packet_for(job)
            accepted = ROOT / job["accepted_file"]
            receipt = runner.readj(ROOT / job["receipt_file"], {})
            if (not accepted.is_file() or runner.digest(accepted.read_bytes()) != job["accepted_source_sha256"]
                    or receipt.get("status") != "accepted" or receipt.get("source_sha256") != job["accepted_source_sha256"]):
                raise ValueError("Accepted artifact or Lean receipt changed")
        return self.status(job_id)

    def run(self, job_id, max_steps=MAX_ATTEMPTS):
        state = self.check_resume(job_id)
        deadline = time.monotonic() + JOB_SECONDS
        for _ in range(max_steps):
            if state["phase"] in TERMINAL or time.monotonic() >= deadline:
                break
            if state["phase"] == "ready":
                state = self.propose(job_id)
            if state["phase"] == "candidate":
                state = self.verify(job_id)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("enqueue")
    add.add_argument("job_id")
    add.add_argument("--theorem", choices=runner.THEOREMS, default="reaches_self")
    add.add_argument("--mode", choices=["replay", "codex"], default="replay")
    for name in ("run", "status", "packet"):
        p = sub.add_parser(name)
        p.add_argument("job_id")
        if name == "run":
            p.add_argument("--max-steps", type=int, choices=range(1, MAX_ATTEMPTS + 1), default=MAX_ATTEMPTS)
    args = parser.parse_args()
    with process_lock():
        store = Store()
        try:
            worker = Worker(store)
            if args.command == "enqueue":
                result = worker.enqueue(args.job_id, args.theorem, args.mode)
            elif args.command == "run":
                result = worker.run(args.job_id, args.max_steps)
            elif args.command == "packet":
                result = packet_for(store.get(args.job_id))
            else:
                result = worker.status(args.job_id)
            print(json.dumps(result, indent=2, ensure_ascii=True))
        finally:
            store.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "stopped", "reason": str(exc)}), file=sys.stderr)
        raise SystemExit(2)

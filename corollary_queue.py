"""Bounded serial intake for up to three reviewed, frozen corollary packets.

Uses the existing loop-state.sqlite3 proposal ledger and runner.py Lean budget.
No target discovery, new allowance, account setup, model download or lease grant.
Only small standalone Lean/Std packets are supported in this first adapter.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import socket
import time

import luna_loop as loop
import runner

MAX_JOBS = 3
MAX_ATTEMPTS = 3
MAX_QUEUE_SECONDS = 900
SLUG = re.compile(r"[a-z][a-z0-9_-]{0,19}\Z")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
IMPORTS = {"Init", "Std", "Std.Tactic"}
SOURCE_FORBIDDEN = re.compile(r"\b(axiom|sorry|admit|unsafe|native_decide|run_tac|elab|macro)\b|#", re.I)
STATEMENT_FORBIDDEN = re.compile(r"\b(theorem|lemma|def|example|instance|opaque|constant|namespace|end|import|set_option)\b|:=|/\-|--")
TERMINAL = {"accepted", "attempt_limit", "provider_error", "needs_review"}


def utc(value):
    if not isinstance(value, str):
        raise ValueError("UTC timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def host():
    return socket.gethostname().casefold()


def exact(obj, keys, label):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError(label + " fields do not match the packet contract")


def safe_file(base, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("Packet paths must be relative POSIX paths")
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts):
        raise ValueError("Packet path escapes its directory")
    if path.as_posix() != relative or not re.fullmatch(r"[A-Za-z0-9_./-]+", relative):
        raise ValueError("Noncanonical packet path")
    current = base.resolve()
    for part in path.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Linked packet paths are not supported")
    if not current.resolve().is_relative_to(base.resolve()) or not current.is_file():
        raise ValueError("Missing or escaped packet file")
    return current


def frozen_file(base, spec):
    exact(spec, {"path", "sha256"}, "Frozen file")
    if not isinstance(spec["sha256"], str) or not HEX.fullmatch(spec["sha256"]):
        raise ValueError("Frozen file requires SHA-256")
    path = safe_file(base, spec["path"])
    if path.suffix != ".lean":
        raise ValueError("Frozen source inputs must be .lean files")
    if path.stat().st_size > 65536:
        raise ValueError("Frozen source file too large for this adapter")
    raw = path.read_bytes()
    if runner.digest(raw) != spec["sha256"]:
        raise ValueError("Frozen file hash mismatch: " + spec["path"])
    return raw


def source_bundle(base, target):
    pieces, imports = [], set()
    files = [dep["source"] for dep in target["dependencies"]] + [target["source"]]
    if len({x["path"] for x in files}) != len(files):
        raise ValueError("Repeated source file in target")
    for spec in files:
        text = frozen_file(base, spec).decode("utf-8")
        if SOURCE_FORBIDDEN.search(text):
            raise ValueError("Reviewed source contains unsupported commands or assumptions")
        kept = []
        for line in text.splitlines():
            if re.match(r"\s*import\b", line):
                match = re.fullmatch(r"import ([A-Za-z0-9_.]+)", line.strip())
                if not match or match[1] not in IMPORTS:
                    raise ValueError("Only pinned Lean/Std imports are supported; supply a standalone packet")
                imports.add(match[1])
            else:
                kept.append(line)
        pieces.append("\n".join(kept))
    source = "".join("import " + name + "\n" for name in sorted(imports))
    source += "\n\n".join(pieces) + "\n"
    if len(source) > 5000:
        raise ValueError("Complete source context exceeds the small-packet limit")
    return source


def validate_packet(packet, base, check_fresh=True):
    exact(packet, {"schema_version", "queue_id", "targets"}, "Queue")
    if type(packet["schema_version"]) is not int or packet["schema_version"] != 1:
        raise ValueError("Unsupported queue schema")
    if not isinstance(packet["queue_id"], str) or not SLUG.fullmatch(packet["queue_id"]):
        raise ValueError("Invalid queue id")
    if not isinstance(packet["targets"], list) or not 1 <= len(packet["targets"]) <= MAX_JOBS:
        raise ValueError("Queue must have one to three frozen targets")
    ids, endpoints = set(), set()
    for target in packet["targets"]:
        exact(target, {"id", "endpoint", "statement", "source", "dependencies", "origin", "ownership", "reviewed_by"}, "Target")
        if not isinstance(target["id"], str) or not SLUG.fullmatch(target["id"]) or target["id"] in ids:
            raise ValueError("Invalid or duplicate target id")
        ids.add(target["id"])
        endpoint = target["endpoint"]
        if not isinstance(endpoint, str) or not NAME.fullmatch(endpoint) or endpoint in endpoints:
            raise ValueError("Invalid or duplicate endpoint")
        if endpoint in {"JevPilot." + n for n in runner.THEOREMS}:
            raise ValueError("Original accepted helper targets cannot be dispatched again")
        endpoints.add(endpoint)
        statement = target["statement"]
        if (not isinstance(statement, str) or not 1 <= len(statement) <= 1500
                or statement != statement.strip() or SOURCE_FORBIDDEN.search(statement)
                or STATEMENT_FORBIDDEN.search(statement)):
            raise ValueError("Invalid frozen theorem statement")
        if not isinstance(target["reviewed_by"], str) or not target["reviewed_by"].strip():
            raise ValueError("Source/statement review attribution required")
        exact(target["origin"], {"repository", "revision"}, "Origin")
        if (not isinstance(target["origin"]["repository"], str) or not target["origin"]["repository"]
                or not isinstance(target["origin"]["revision"], str)
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", target["origin"]["revision"])):
            raise ValueError("Exact source repository/revision required")
        owner = target["ownership"]
        exact(owner, {"status", "already_proven", "coordinator", "evidence", "confirmed_utc", "valid_until_utc"}, "Ownership")
        if (owner["status"] != "unowned" or owner["already_proven"] is not False
                or not isinstance(owner["coordinator"], str) or not owner["coordinator"].strip()
                or not isinstance(owner["evidence"], str) or not owner["evidence"].strip()):
            raise ValueError("Coordinator must identify an unowned, unproved target with evidence")
        confirmed, expires = utc(owner["confirmed_utc"]), utc(owner["valid_until_utc"])
        if not 0 < (expires - confirmed).total_seconds() <= 86400:
            raise ValueError("Ownership attestation may cover at most 24 hours")
        if check_fresh and not confirmed <= datetime.now(timezone.utc) < expires:
            raise ValueError("Ownership attestation is not current")
        if not isinstance(target["dependencies"], list) or len(target["dependencies"]) > 8:
            raise ValueError("Too many dependency files")
        deps = set()
        for dep in target["dependencies"]:
            exact(dep, {"source", "endpoints"}, "Dependency")
            if not isinstance(dep["endpoints"], list) or not 1 <= len(dep["endpoints"]) <= 12:
                raise ValueError("Dependency endpoints required")
            for name in dep["endpoints"]:
                if not isinstance(name, str) or not NAME.fullmatch(name) or name in deps or name == endpoint:
                    raise ValueError("Invalid or repeated dependency endpoint")
                deps.add(name)
        source_bundle(base, target)
    # Targets are independent packets; they may not silently depend on each other.
    for target in packet["targets"]:
        if any(name in endpoints for dep in target["dependencies"] for name in dep["endpoints"]):
            raise ValueError("Cross-job dependencies require separate reviewed intake")
    return packet


def goal_key(target):
    return loop.sha({k: target[k] for k in ("origin", "endpoint", "statement")})


class Queue:
    def __init__(self, store, provider=None):
        self.store, self.root, self.provider = store, store.root.resolve(), provider
        if self.root != loop.ROOT.resolve() or self.root != runner.ROOT.resolve():
            raise ValueError("Queue and existing budget owners must use the same root")
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS corollary_queues (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS corollary_targets (
                goal_key TEXT PRIMARY KEY, queue_id TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE);
        """)

    def get(self, queue_id):
        row = self.store.db.execute("SELECT data FROM corollary_queues WHERE id=?", (queue_id,)).fetchone()
        if not row:
            raise ValueError("Unknown queue; admit exact target packets first")
        return json.loads(row[0])

    def prepare(self, manifest_path):
        """Validate and freeze reviewed intake; never authorizes or invokes providers."""
        manifest_path = Path(manifest_path).resolve()
        packet = validate_packet(json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path.parent)
        fingerprint = loop.sha(packet)
        queue_id = packet["queue_id"]
        try:
            old = self.get(queue_id)
        except ValueError:
            old = None
        if old:
            if old["packet_sha256"] != fingerprint:
                raise ValueError("Existing queue identity differs; refusing replacement")
            self.packet(old, check_fresh=False)
            return self.status(queue_id)
        for target in packet["targets"]:
            if self.store.db.execute("SELECT 1 FROM corollary_targets WHERE goal_key=?", (goal_key(target),)).fetchone():
                raise ValueError("Target already admitted; use its original queue")
        inputs = self.root / "corollary-inputs"
        if (inputs.is_symlink() or (hasattr(inputs, "is_junction") and inputs.is_junction())
                or not inputs.resolve().is_relative_to(self.root)):
            raise ValueError("Frozen input directory must stay inside the authoritative root")
        directory = inputs / queue_id
        directory.mkdir(parents=True, exist_ok=False)
        try:
            for target in packet["targets"]:
                for spec in [d["source"] for d in target["dependencies"]] + [target["source"]]:
                    raw = frozen_file(manifest_path.parent, spec)
                    dest = directory / spec["path"]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists() and dest.read_bytes() != raw:
                        raise ValueError("Conflicting frozen paths across targets")
                    dest.write_bytes(raw)
            runner.writej(directory / "packet.json", packet)
            jobs = ["cq-" + queue_id + "-" + t["id"] for t in packet["targets"]]
            record = {"id": queue_id, "packet_sha256": fingerprint, "packet_path": (directory / "packet.json").relative_to(self.root).as_posix(),
                      "jobs": jobs, "created_utc": runner.now()}
            # No proposal or Lean balance is initialized here.
            with self.store.db:
                self.store.db.execute("INSERT INTO corollary_queues VALUES (?,?)", (queue_id, loop.canonical(record)))
                for target, job_id in zip(packet["targets"], jobs):
                    job = {"id": job_id, "queue_id": queue_id, "target_id": target["id"], "theorem": target["endpoint"],
                           "mode": "codex", "phase": "ready", "attempt": 0, "task_sha256": loop.sha(target),
                           "max_attempts": MAX_ATTEMPTS, "created_utc": runner.now(), "history": [], "lean_reservations": []}
                    self.store.db.execute("INSERT INTO jobs VALUES (?,?)", (job_id, loop.canonical(job)))
                    self.store.db.execute("INSERT INTO corollary_targets VALUES (?,?,?)", (goal_key(target), queue_id, job_id))
        except BaseException:
            # Preserve partially written intake for explicit reconciliation, never silently overwrite.
            raise
        return self.status(queue_id)

    def packet(self, record, check_fresh=True):
        path = safe_file(self.root, record["packet_path"])
        packet = json.loads(path.read_text(encoding="utf-8"))
        if loop.sha(packet) != record["packet_sha256"]:
            raise ValueError("Frozen packet identity changed")
        validate_packet(packet, path.parent, check_fresh)
        if (packet["queue_id"] != record["id"]
                or record["jobs"] != ["cq-" + record["id"] + "-" + t["id"] for t in packet["targets"]]):
            raise ValueError("Queue membership changed")
        return packet

    def usage(self, record):
        placeholders = ",".join("?" for _ in record["jobs"])
        calls = self.store.db.execute("SELECT count(*) FROM proposals WHERE mode='codex' AND job_id IN (" + placeholders + ")", record["jobs"]).fetchone()[0]
        checks = sum(len(self.store.get(j)["lean_reservations"]) for j in record["jobs"])
        return {"provider_reservations": calls, "verification_reservations": checks}

    def status(self, queue_id):
        record = self.get(queue_id)
        jobs = [self.store.get(j) for j in record["jobs"]]
        return {"queue_id": queue_id, "packet_sha256": record["packet_sha256"], **self.usage(record),
                "jobs": [{k: job[k] for k in ("id", "target_id", "phase", "attempt")} for job in jobs]}

    def authorization(self, record, required_seconds):
        auth = runner.readj(self.root / "loop-authorization.json", {})
        grant = auth.get("corollary_queues", {}).get(record["id"])
        if not isinstance(grant, dict) or not auth.get("approved_by") or not auth.get("approved_utc"):
            raise RuntimeError("No finite corollary execution authorization")
        exact(grant, {"packet_sha256", "max_provider_calls", "max_lean_checks", "max_attempts_per_job", "deadline_utc", "lease_id"}, "Queue authorization")
        if grant["packet_sha256"] != record["packet_sha256"]:
            raise RuntimeError("Authorization does not cover this exact queue")
        for key, limit in (("max_provider_calls", 9), ("max_lean_checks", 9), ("max_attempts_per_job", MAX_ATTEMPTS)):
            if type(grant[key]) is not int or not 1 <= grant[key] <= limit:
                raise RuntimeError("Invalid finite execution cap: " + key)
        if (utc(grant["deadline_utc"]) - datetime.now(timezone.utc)).total_seconds() < required_seconds:
            raise RuntimeError("Queue execution window has insufficient time")
        lease = runner.readj(self.root / "corollary-lease.local.json", {})
        exact(lease, {"lease_id", "queue_id", "packet_sha256", "host", "root", "holder", "granted_by", "resources", "issued_utc", "expires_utc", "lean_sha256"}, "Shared resource lease")
        if (lease["lease_id"] != grant["lease_id"] or lease["queue_id"] != record["id"]
                or lease["packet_sha256"] != record["packet_sha256"] or lease["host"].casefold() != host()
                or Path(lease["root"]).resolve() != self.root or lease["holder"] != "corollary-queue"
                or not lease["granted_by"] or lease["resources"] != ["luna_provider", "lean_compiler"]):
            raise RuntimeError("Lease does not cover this dispatcher and both resources")
        start, end = utc(lease["issued_utc"]), utc(lease["expires_utc"])
        now = datetime.now(timezone.utc)
        if not start <= now < end or (end-now).total_seconds() < required_seconds or (end-start).total_seconds() > MAX_QUEUE_SECONDS:
            raise RuntimeError("Shared provider/compiler lease is expired or too short")
        if not runner.LEAN.is_file() or runner.digest(runner.LEAN.read_bytes()) != lease["lean_sha256"]:
            raise RuntimeError("Pinned compiler executable identity changed or is missing")
        return grant, lease

    def preflight(self, record, job, required_seconds):
        if (self.root / "STOP").exists():
            raise RuntimeError("Paused by STOP file")
        packet = self.packet(record)
        target = next(t for t in packet["targets"] if t["id"] == job["target_id"])
        if loop.sha(target) != job["task_sha256"]:
            raise ValueError("Target identity differs from the admitted job")
        grant, lease = self.authorization(record, required_seconds)
        used = self.usage(record)
        if runner.state_now()["checks_used"] >= runner.LIMIT or used["verification_reservations"] >= grant["max_lean_checks"]:
            raise RuntimeError("Global or queue Lean cap exhausted")
        if job["phase"] == "ready" and (used["provider_reservations"] >= grant["max_provider_calls"]
                                        or job["attempt"] >= grant["max_attempts_per_job"]):
            raise RuntimeError("Queue provider or per-job attempt cap exhausted")
        # packet() revalidates every target again before verification, including
        # pending/completed siblings. Each clearance must outlast this operation
        # before a proposal or compiler reservation can consume scarce allowance.
        now = datetime.now(timezone.utc)
        if any((utc(t["ownership"]["valid_until_utc"]) - now).total_seconds() < required_seconds
               for t in packet["targets"]):
            raise RuntimeError("Ownership clearance has insufficient time for the bounded operation")
        return target, grant, lease

    def prompt(self, record, target, job):
        base = (self.root / record["packet_path"]).parent
        packet = {"instruction": "Return only JSON with one proof field containing a Lean 4 by block. "
                  "Use no tools, delegation, imports, commands, sorry, new axioms or metaprogramming. "
                  "Keep the frozen theorem unchanged; an independent external Lean check decides acceptance.",
                  "lean_version": "4.33.1", "goal": "theorem " + target["endpoint"] + " " + target["statement"],
                  "source_context": source_bundle(base, target), "target_sha256": loop.sha(target),
                  "last_proof": job.get("proof", ""), "last_lean_error": job.get("feedback", "")[:loop.MAX_ERROR]}
        if len(loop.canonical(packet)) > loop.MAX_PROMPT:
            raise ValueError("Prompt exceeds the existing bounded context limit")
        return packet

    def propose(self, record, job):
        target, grant, lease = self.preflight(record, job, loop.CALL_SECONDS + runner.CALL_SECONDS + 20)
        packet = self.prompt(record, target, job)
        self.store.reserve(job, packet)  # Same global proposal ledger as the original loop.
        directory = self.root / "loop-runs" / job["id"] / str(job["attempt"])
        directory.mkdir(parents=True, exist_ok=False)
        runner.writej(directory / "request.json", packet)
        try:
            result = (self.provider or loop.CodexProvider())(packet, job, directory)
            proof = result.get("proof")
            if not isinstance(proof, str) or len(proof) > loop.MAX_PROOF or not runner.candidate_ok(proof)[0]:
                raise ValueError("Proof rejected by the existing restricted candidate policy")
            job.update(phase="candidate", proof=proof, proof_sha256=runner.digest(proof.encode("utf-8")), lease_id=lease["lease_id"])
            runner.writej(directory / "provider-result.json", result)
            self.store.proposal_result(job, result)
        except Exception as exc:
            job.update(phase="provider_error", feedback=str(exc))
            self.store.proposal_result(job, {"error": str(exc), "reservation_spent": True})
            raise

    def verify(self, record, job):
        target, grant, lease = self.preflight(record, job, runner.CALL_SECONDS + 10)
        proof = job["proof"]
        if not runner.candidate_ok(proof)[0] or runner.digest(proof.encode("utf-8")) != job["proof_sha256"]:
            raise ValueError("Candidate identity changed")
        base = (self.root / record["packet_path"]).parent
        endpoints = [name for dep in target["dependencies"] for name in dep["endpoints"]] + [target["endpoint"]]
        source = source_bundle(base, target) + "\ntheorem " + target["endpoint"] + " " + target["statement"] + " := " + proof + "\n"
        source += "".join("#print axioms " + name + "\n" for name in endpoints)
        reservation = {"attempt": job["attempt"], "source_sha256": runner.digest(source.encode("utf-8")),
                       "reserved_utc": runner.now(), "lease_id": lease["lease_id"], "host": host()}
        job["lean_reservations"].append(reservation)
        job["phase"] = "checking"
        self.store.save(job)
        # dispatch() already owns runner.lock for the full provider/compiler lease.
        result = runner.check(source, job["id"] + "-" + str(job["attempt"]), endpoints)
        receipt_path = self.root / "attempts" / (result["id"] + ".result.json")
        if (result.get("source_sha256") != reservation["source_sha256"] or result.get("endpoints") != endpoints
                or not receipt_path.is_file() or runner.readj(receipt_path, {}) != result):
            raise ValueError("Compiler receipt does not match this frozen attempt")
        reservation.update(receipt_file=receipt_path.relative_to(self.root).as_posix(), receipt_sha256=runner.digest(receipt_path.read_bytes()))
        job["history"].append({"attempt": job["attempt"], "check_id": result["id"], "status": result["status"]})
        job["feedback"] = result["output"][:loop.MAX_ERROR]
        job["phase"] = "accepted" if result["status"] == "accepted" else (
            "attempt_limit" if job["attempt"] >= grant["max_attempts_per_job"] else "ready")
        if job["phase"] == "accepted":
            self.check_accepted_result(result, endpoints)
            artifact = self.root / "loop-runs" / job["id"] / "Accepted.lean"
            artifact.write_text(source, encoding="utf-8", newline="\n")
            job.update(accepted_file=artifact.relative_to(self.root).as_posix(), accepted_source_sha256=reservation["source_sha256"])
        self.store.save(job)

    @staticmethod
    def check_accepted_result(result, endpoints):
        reports = result.get("axioms", {})
        if (result.get("status") != "accepted" or result.get("lean_exit_code") != 0
                or result.get("missing_axiom_reports") or result.get("disallowed_axioms")
                or any(name not in reports or not set(reports[name]) <= runner.ALLOWED_AXIOMS for name in endpoints)):
            raise ValueError("Accepted receipt lacks exact endpoint axiom evidence")

    def reconcile(self, record):
        packet = self.packet(record, check_fresh=False)
        for job_id, target in zip(record["jobs"], packet["targets"]):
            job = self.store.get(job_id)
            if job["task_sha256"] != loop.sha(target):
                raise ValueError("Stored job and frozen target disagree")
            if job["phase"] in {"proposal_reserved", "checking"}:
                job.update(phase="needs_review", feedback="Interrupted operation: reconcile saved receipts; reservations remain spent.")
                self.store.save(job)
            if job["phase"] == "accepted":
                last = job["lean_reservations"][-1]
                artifact = safe_file(self.root, job["accepted_file"])
                receipt_path = safe_file(self.root, last["receipt_file"])
                receipt = runner.readj(receipt_path, {})
                endpoints = [n for d in target["dependencies"] for n in d["endpoints"]] + [target["endpoint"]]
                if (runner.digest(artifact.read_bytes()) != job["accepted_source_sha256"]
                        or receipt.get("source_sha256") != job["accepted_source_sha256"]
                        or receipt.get("endpoints") != endpoints
                        or runner.digest(receipt_path.read_bytes()) != last["receipt_sha256"]):
                    raise ValueError("Accepted artifact or compiler receipt changed")
                self.check_accepted_result(receipt, endpoints)

    def dispatch(self, queue_id):
        record = self.get(queue_id)
        # Same lock as legacy Luna/ADK workers; one runner lock for both resources.
        with loop.process_lock(self.root), runner.lock():
            self.reconcile(record)
            jobs = [self.store.get(j) for j in record["jobs"]]
            if any(j["phase"] in {"needs_review", "provider_error"} for j in jobs):
                raise RuntimeError("Uncertain prior operation needs review before any further dispatch")
            if all(j["phase"] in TERMINAL for j in jobs):
                return self.status(queue_id)
            started = time.monotonic()
            for job_id in record["jobs"]:
                job = self.store.get(job_id)
                for _ in range(MAX_ATTEMPTS):
                    if job["phase"] in TERMINAL:
                        break
                    required = loop.CALL_SECONDS + runner.CALL_SECONDS + 20 if job["phase"] == "ready" else runner.CALL_SECONDS + 10
                    if time.monotonic() - started + required > MAX_QUEUE_SECONDS:
                        return self.status(queue_id)
                    if job["phase"] == "ready":
                        self.propose(record, job)
                        job = self.store.get(job_id)
                    if job["phase"] == "candidate":
                        self.verify(record, job)
                        job = self.store.get(job_id)
        return self.status(queue_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate").add_argument("packet", type=Path)
    sub.add_parser("prepare").add_argument("packet", type=Path)
    sub.add_parser("status").add_argument("queue_id")
    sub.add_parser("dispatch").add_argument("queue_id")
    args = parser.parse_args()
    if args.command == "validate":
        packet = validate_packet(json.loads(args.packet.read_text(encoding="utf-8")), args.packet.resolve().parent)
        print(json.dumps({"status": "validated_only", "queue_id": packet["queue_id"], "packet_sha256": loop.sha(packet), "targets": len(packet["targets"]), "execution_authorized": False}))
        return
    store = loop.Store(loop.ROOT)
    try:
        queue = Queue(store)
        if args.command == "prepare":
            with loop.process_lock(loop.ROOT):
                result = queue.prepare(args.packet)
        elif args.command == "status":
            result = queue.status(args.queue_id)
        else:
            result = queue.dispatch(args.queue_id)
        print(json.dumps(result, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()

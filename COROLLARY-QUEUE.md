# Three-job corollary queue

Version 2, 25 September 2026: ownership clearance must cover the full bounded
operation before reserving either a proposal or a compiler attempt. This check
includes every attestation in the frozen packet, because verification checks
the whole packet again. Version 1 and its audit evidence remain preserved in
the earlier immutable handoff archive; do not use that version for dispatch.

This is the follow-on intake/dispatch adapter requested through the coordinating
publication task on 25 September 2026. It processes up to three frozen targets
serially using the existing proposal database and Lean budget. It does not
discover targets, grant itself a lease or allowance, or start another worker.
No actual desktop corollary packet has been admitted or executed in this task.

The original worker and ADK replay remain unchanged. This adapter reuses their
Codex Luna provider, spending reservation code, process lock, restricted proof
filter and Lean receipt checks. The adapter itself needs only Python's standard
library. It does not invoke TypeSafe or require an account key. The local Laya
decision experiment remains separate; this adapter does not claim to generate
Lean proofs with Laya or to route via Antigravity.

## Current verification and limitations

The offline tests use synthetic packets and mock every provider/compiler call
inside temporary directories. They exercise real reservation, file-integrity,
queue, lock and receipt logic. They are not mathematical proof results, fresh
Luna responses or an Atlas execution test. See `QUEUE-VERIFICATION-v2.json` for
the version 2 test receipt and before/after live-ledger fingerprints.

The first intake adapter deliberately accepts small standalone Lean 4.33.1
packets. Each dependency and source file is frozen by SHA-256; their combined
context is limited to 5,000 characters. Imports are restricted to `Init`, `Std`
and `Std.Tactic`. Mathlib/project imports, external build commands, arbitrary
Lean metaprogramming, cross-job dependencies and larger project contexts are
not supported. The coordinator must supply a reviewed standalone packet or
identify the missing capability before attempting those targets. Do not call
an unsupported packet executable merely because it passed repository review.

Source review and coordinator ownership assertions are trusted local inputs.
The lexical policy is not a sandbox for hostile Lean. Only Lean's successful
check of the exact endpoint and dependency axiom reports can establish
acceptance, within the explicitly trusted compiler and allowed axioms.

## Intake contract

The coordinator supplies a JSON manifest and its local `.lean` files. No
example in this document is a real research target or an execution grant.

Top-level fields are exactly `schema_version` (1), `queue_id` (a lowercase
identifier of at most 20 characters), and `targets` (one to three entries).
Each target has exactly these fields:

| Field | Required content |
| --- | --- |
| `id` | Unique lowercase identifier, at most 20 characters. |
| `endpoint` | Exact Lean declaration name; each component uses ASCII identifiers. |
| `statement` | Complete parameters and proposition following the theorem name, unchanged by the worker. No proof or declaration commands. |
| `source` | Object with relative POSIX `path` to a `.lean` file and lowercase `sha256`. This supplies the target's definitions/context, not its target proof. |
| `dependencies` | Ordered list of objects, each with `source` in the same path/hash format and a nonempty `endpoints` list. All declared dependency endpoints are checked again with the target. |
| `origin` | Object with `repository` and exact 40- or 64-character hexadecimal `revision`. This records provenance; it does not fetch a repository. |
| `ownership` | Object with `status: "unowned"`, `already_proven: false`, nonempty `coordinator` and `evidence`, and timezone-qualified `confirmed_utc` / `valid_until_utc`. The attestation can cover at most 24 hours. |
| `reviewed_by` | Nonempty attribution for review of the exact source, statement and dependency packet. |

Sources must stay within the supplied manifest directory. Absolute paths,
parent traversal, links/junctions, conflicting file hashes and unexpected
fields are rejected. The source/dependency text is combined in the specified
order, with its allowed standard-library imports placed at the top. The exact
target declaration and independently supplied `by` proof are appended, followed
by axiom-print commands for every dependency endpoint and the target.

Validation alone is read-only and has no provider/compiler calls:

```powershell
python .\corollary_queue.py validate .\reviewed-intake\packet.json
```

After the coordinator supplies the real packets, preparation freezes their
bytes under `corollary-inputs/<queue_id>/` and adds records to the existing
`loop-state.sqlite3`. It does not create or reset an allowance:

```powershell
python .\corollary_queue.py prepare .\reviewed-intake\packet.json
python .\corollary_queue.py status desktop-corollaries
```

The same packet can be prepared again without duplicate jobs. A changed
statement/source/ownership packet cannot replace an admitted queue. A goal
already admitted under the same origin, endpoint and statement cannot be
reintroduced by renaming the queue. Original accepted pilot helper endpoints
are also rejected. Expired or partially written intake needs explicit
reconciliation; do not delete its record to manufacture a fresh attempt.

## One authority and shared resources

The existing `loop-authorization.json` remains the only proposal allowance.
Its cumulative `additional_luna_calls` is consumed by the same `proposals`
table used by legacy jobs. The existing `state.json` and `runner.py` remain the
only global Lean check budget. Per-job attempt histories are used to derive
queue ceilings; no second remaining-balance file is created.

An explicit finite grant must additionally be present under
`loop-authorization.json` -> `corollary_queues` -> `<queue_id>`. It has exactly:

- `packet_sha256`: the canonical packet digest reported by validation.
- `max_provider_calls` and `max_lean_checks`: positive aggregate caps, each
  no greater than 9 and subordinate to the existing global allowances.
- `max_attempts_per_job`: a positive cap no greater than 3.
- `deadline_utc`: an absolute expiry for the authorized execution window.
- `lease_id`: the exact resource lease assigned by the coordinator.

Those upper bounds are validation limits, not authorization. This work has
not written any such live grant. The current additional Luna allowance is 0.

The coordinator must also provide `corollary-lease.local.json` with exactly
`lease_id`, `queue_id`, `packet_sha256`, `host`, `root`,
`holder: "corollary-queue"`, `granted_by`,
`resources: ["luna_provider", "lean_compiler"]`, `issued_utc`, `expires_utc`,
and the pinned executable's `lean_sha256`. The host and absolute root must
match the actual authoritative process; copying a lease to another computer
or directory does not make it valid. A lease lasts at most 900 seconds and
must leave time for the next bounded operation before it can begin.

The dispatcher holds the original Luna process lock and `runner.lock` across
the full serial run, including provider waits. Those locks exclude other
workers in this pilot. The coordinator lease represents availability of the
shared research resources; it is not a distributed lock for unrelated agents
that ignore this protocol. The coordinator must confirm the actual Atlas
compiler/provider owner before issuing it. The adapter cannot seize or revoke
another research task's lease.

After exact targets, host configuration, resource availability and finite
authorization have been resolved, the available dispatch commands are:

```powershell
python .\corollary_queue.py dispatch desktop-corollaries
.\start-corollary-queue.ps1 -QueueId desktop-corollaries
```

The PowerShell launcher uses a hidden process and saves stdout/stderr. It
creates no authorization. `STOP` prevents new operations; the existing provider
also watches it during a call. Provider calls retain the 90-second timeout and
compiler calls the 25-second timeout. Every ownership attestation must have at
least 135 seconds remaining before a proposal (90 + 25 + 20 seconds of margin),
and 35 seconds before verification (25 + 10). A short-lived sibling clearance
blocks spending on the current job, even when its own clearance is adequate.
An expired or insufficient clearance requires explicit intake reconciliation;
the worker does not renew or rewrite it. A dispatch run is bounded to 900 seconds
and starts no operation without its reserved time margin. Source, ownership,
budget and lease are checked again before each proposal and compiler call.

Failed or interrupted provider operations retain their spending reservation
and stop further queue dispatch for review. A process interrupted while
compiling similarly requires reconciliation. A rejected proof can be repaired
only within both job and aggregate caps. Successful artifacts and compiler
receipts are hashed and checked on resume; completed jobs make no new calls.

## Publication and host handoff

The existing core publication snapshot is listed in
`PUBLICATION-SOURCE-MANIFEST.json`. The queue is a separate follow-on source
delta. Version 2 is described by `QUEUE-SOURCE-ADDENDUM-v2.json` and the immutable
`corollary-queue-v2-20260925.zip`; retain the original addendum as version 1
evidence. The publication owner should add these ignore entries when including it:

```gitignore
corollary-inputs/
corollary-lease.local.json
```

Keep live SQLite files, mutable budget/authorization files, private target
packets, local runtime paths, lease files and raw provider logs out of the
publication. Evidence copies must be sanitized and labelled read-only.
No live budget or database should be cloned to Atlas. A host move requires a
single-owner handoff with the old dispatcher stopped and receipts reconciled.
The published runner uses `PILOT_LEAN_EXE` and `PILOT_CANON_SOURCE` for destination paths.
Configure them and verify the Lean binary and frozen source before any new host execution.

# Luna / local decisions / ADK / Lean pilot

This repository contains a bounded experimental workflow for mathematical proof proposals and Lean verification. It also contains a separate local typed-decision experiment using open Laya checkpoints. The two model roles are distinct: a decision model may advise among a fixed set of actions; Lean decides whether a proposed proof of the exact theorem compiles under the allowed axiom policy.

## Source map

| File | Purpose |
| --- | --- |
| `decision_cli.py`, `laya_probe.py` | Finite local Laya decision calls and evaluation harness. |
| `luna_loop.py` | Durable proposal/check/repair state machine and provider adapter. |
| `adk_loop.py` | Google ADK 2.6 graph workflow around the same state machine. |
| `runner.py` | Frozen Lean statement verification and axiom inspection. |
| `corollary_queue.py` | Serial intake/dispatch of up to three reviewed standalone Lean/Std packets. |
| `test_luna_loop.py`, `test_corollary_queue.py` | Offline controls with mocked model and compiler calls. |

The local models, credentials, mutable ledgers, machine configuration, proof-attempt logs and private research packets are intentionally absent. `loop-authorization.example.json` permits zero live provider calls. Do not treat this public copy as an execution grant or a second authoritative worker.

The live Codex adapter also references a local `loop-worker-context/AGENTS.md` instruction file. The original internal policy file is not published here; this repository is therefore intended for source review and offline controls until an authorized deployment supplies its own reviewed worker context and execution grant.

## Reproducing the offline controls

Use Python 3.12. From the repository root:

```powershell
python -m unittest -v test_luna_loop test_corollary_queue
```

These controls use temporary directories and mock provider/compiler calls. They do not establish a new mathematical result. The corrected queue version and original loop passed 33 such controls in the public staging copy. An independent offline review reproduced and closed an ownership-expiry edge case for the reviewed queue version.

The original proof pilot uses Lean 4.33.1. Configure `PILOT_LEAN_EXE` and `PILOT_CANON_SOURCE` for a destination before any independent execution. The queue currently accepts only small standalone Lean/Std packets with at most 5,000 characters of combined source context; Mathlib project builds and cross-job dependencies are outside this adapter. See `COROLLARY-QUEUE.md` for the packet contract.

Google ADK coordinates deterministic workflow nodes; it is not a proof judge and does not require a Google model for its local control flow. The official TypeSafe Jev endpoint is hosted and is not used as the on-device model in this repository. Local Laya is an independent open model family; its advice does not replace Lean verification.

References: [Google ADK loop workflows](https://adk.dev/agents/workflow-agents/loop-agents/), [Laya source](https://github.com/NandhaKishorM/laya), [TypeSafe model interface](https://docs.typesafe.ai/models).

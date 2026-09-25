# Architecture and validation boundary

```
reviewed theorem packet
  -> frozen source and statement hash
  -> one serial queue and durable attempt reservation
  -> bounded proof proposal
  -> candidate shape and token checks
  -> Lean compiler plus exact axiom reports
  -> accepted receipt, bounded repair, or review stop
```

The Python worker owns one SQLite job ledger, a process lock, and a separate Lean-check ledger. It records a model attempt before invoking the provider and records a compiler check before invoking Lean. Interrupted operations require explicit reconciliation. Hashes bind each job to its source, statement, proof and accepted receipt. The ADK workflow executes proposal and verification nodes serially with `max_concurrency=1`; its in-memory session is not the spending authority.

The corollary adapter adds packet validation and a three-entry serial queue. It requires a reviewed ownership attestation, exact source hashes, an aggregate grant and a host-bound resource lease before dispatch. The adapter cannot discover which research problem is unowned or reserve a shared compiler used by unrelated agents. It accepts standalone Lean/Std inputs only. The public example grant authorizes no live calls.

Local Laya may provide typed strategy advice in a separate finite-batch experiment. The code must validate its output against an allowed choice set and abstain on invalid results. Laya is not used to generate proof text or certify a theorem in this workflow. A proof is accepted only after Lean checks the unchanged statement and all expected endpoint axiom reports.

The offline controls cover reservation, caps, duplicate intake, source tampering, lease mismatch, serial order, rejection, accepted receipt integrity and no-spend resume. They mock the model and compiler. A real research claim still requires the pinned Lean environment, the exact theorem receipt and admission by its canonical research owner.

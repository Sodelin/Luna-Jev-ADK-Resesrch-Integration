"""Offline, small decision-model probe. No paid calls, training or server."""
import os, sys, json, time, statistics, hashlib, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = Path(r"<local-model-root>\laya-55cf4c4e")
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", USE_TF="0", TOKENIZERS_PARALLELISM="false")
sys.path.insert(0, str(ROOT / "runtime" / "laya-source"))
import torch
import laya

CASES = [
    ("self", "Prove that a value reaches itself in zero iterations.", "direct"),
    ("commute", "Prove for every natural number n that iterating f n times commutes with one application of f.", "induction"),
    ("predecessor", "A witness says f(x) reaches one in k steps. Construct a witness saying x reaches one in k+1 steps.", "direct"),
    ("boundary", "Prove that every vertex with a parent in the removed list belongs to that list's flatMap of child lists.", "membership"),
    ("empty", "Prove that the empty list has length zero by unfolding the definition.", "direct"),
    ("append", "Prove a statement for all lists by handling the empty list and the cons case with an induction hypothesis.", "induction"),
    ("member", "Given x is in list a, prove x is in the concatenation a ++ b.", "membership"),
    ("rewrite", "Given an equality h : a = b, prove f a = f b by rewriting with h.", "direct"),
]
QUESTION = {"strategy": {"type": "choice", "instructions": "Choose the most relevant first proof strategy for this Lean theorem. This is a routing hint, not a correctness judgment.", "criteria": {
    "direct": "Use an existing witness, unfold a definition, or rewrite an equality directly.",
    "induction": "Use structural induction on a natural number or a list.",
    "membership": "Use list membership lemmas for flatMap or append."}}}

def main():
    torch.set_num_threads(4)
    receipt = json.loads((MODEL / "download-receipt.json").read_text())
    expected = {x["path"]:x["sha256"] for x in receipt["files"] if x["path"] != "README.md"}
    started = time.perf_counter()
    agent = laya.load(str(MODEL), device="cuda", expected_sha256=expected)
    torch.cuda.synchronize()
    loaded = time.perf_counter() - started
    rows = []
    for key, state, expected_label in CASES:
        begin = time.perf_counter()
        output = agent.predict_batch([state], QUESTION)[0]
        torch.cuda.synchronize()
        elapsed = (time.perf_counter() - begin) * 1000
        answer = output["answers"]["strategy"]
        row = dict(id=key, state=state, expected=expected_label, answer=answer,
                   milliseconds=round(elapsed,3), correct=answer["choice"] == expected_label)
        rows.append(row)
        print(json.dumps(row), flush=True)
    result = dict(model=receipt["repo"], revision=receipt["revision"], source_revision=json.loads((ROOT/"runtime/source-receipt.json").read_text())["revision"],
                  device=str(agent.device), gpu=torch.cuda.get_device_name(), torch=torch.__version__,
                  load_seconds=round(loaded,3), peak_allocated_MiB=round(torch.cuda.max_memory_allocated()/2**20,1),
                  correct=sum(r["correct"] for r in rows), count=len(rows),
                  warm_median_ms=round(statistics.median(r["milliseconds"] for r in rows[1:]),3),
                  interpretation="Handwritten feasibility set, not held-out benchmark or calibrated proof-safety estimate.", rows=rows)
    filename="laya-typed-results.json" if receipt["repo"].endswith("typed-decisions") else "laya-results.json"
    (ROOT/filename).write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="rows"}),flush=True)

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',choices=['base','typed'],default='base')
    parser.add_argument('--model-root',type=Path)
    args=parser.parse_args()
    if args.model=='typed':
        MODEL=Path(r'<local-model-root>\laya-typed-1a793eb5')
    if args.model_root:
        MODEL=args.model_root/MODEL.name
    main()

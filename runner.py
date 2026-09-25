#!/usr/bin/env python3
"""Bounded local proof runner for the Jev/Lean feasibility pilot."""
import argparse, hashlib, json, os, re, subprocess, sys, time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEAN = Path(os.environ.get("PILOT_LEAN_EXE", "lean"))
CANON = Path(os.environ.get("PILOT_CANON_SOURCE", str(ROOT / "snapshots" / "RankedCoalescenceSound.lean")))
LIMIT, RUN_SECONDS, CALL_SECONDS = 20, 180, 25
THEOREMS = {
 "reaches_self": "{α : Type} (f : α → α) (one : α) : Reaches f one one",
 "iterate_commute": "{α : Type} (f : α → α) (n : Nat) (x : α) : iterate f n (f x) = f (iterate f n x)",
 "reaches_predecessor": "{α : Type} (f : α → α) (one x : α) (hx : Reaches f one (f x)) : Reaches f one x",
}
DEPENDENCIES = {"reaches_self": [], "iterate_commute": [], "reaches_predecessor": ["iterate_commute"]}
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
FORBIDDEN = re.compile(r"\b(sorry|admit|axiom|unsafe|native_decide|run_tac|elab|macro|import|namespace|end|set_option)\b|#|/\-|--", re.I)
TACTICS = ("by", "exact ", "induction ", "cases ", "simp", "rw ", "rcases ", "refine ", "calc", "|", "_ =", "iterate ")

def now(): return datetime.now(timezone.utc).isoformat()
def digest(data): return hashlib.sha256(data).hexdigest()
def readj(path, default): return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
def writej(path, obj):
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    os.replace(tmp,path)

@contextmanager
def lock():
    path=ROOT/"runner.lock"
    try: fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise SystemExit("Another runner owns runner.lock; refuse concurrent budget use")
    try:
        os.write(fd,str(os.getpid()).encode()); os.close(fd)
        yield
    finally: path.unlink(missing_ok=True)

def log(record):
    with (ROOT/"attempts.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(record,ensure_ascii=False)+"\n"); f.flush(); os.fsync(f.fileno())

def state_now():
    s=readj(ROOT/"state.json",None)
    if not s or type(s.get("checks_used")) is not int or not 0 <= s["checks_used"] <= LIMIT:
        raise SystemExit("Missing or invalid durable check budget")
    return s

def axiom_reports(output, endpoints):
    reports={}
    for match in re.finditer(r"'([^']+)'\s+(?:depends on axioms:\s*\[([^\]]*)\]|does not depend on any axioms)",output,re.S):
        reports[match[1]]=[] if match[2] is None else [a.strip() for a in match[2].split(",") if a.strip()]
    missing=[e for e in endpoints if e not in reports]
    bad={e:reports[e] for e in endpoints if e in reports and not set(reports[e])<=ALLOWED_AXIOMS}
    return reports,missing,bad

def check(source, label, endpoints, seconds=CALL_SECONDS, command=None):
    s=state_now()
    if (ROOT/"STOP").exists(): raise SystemExit("Paused by STOP file")
    if s["checks_used"]>=LIMIT: raise SystemExit("Global Lean check limit reached")
    s["checks_used"]+=1; writej(ROOT/"state.json",s)
    aid=f"{s['checks_used']:02d}-{time.time_ns()}-{label}"
    path=ROOT/"attempts"/(aid+".lean")
    path.write_text(source,encoding="utf-8")
    base=dict(id=aid,label=label,check_number=s["checks_used"],source_sha256=digest(source.encode()),endpoints=endpoints)
    log(dict(base,event="reserved",time_utc=now()))
    start=time.monotonic()
    try:
        p=subprocess.run(command or [str(LEAN),str(path)],cwd=str(ROOT),capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=seconds,shell=False)
        output=p.stdout+p.stderr
        reports,missing,bad=axiom_reports(output,endpoints)
        passed=p.returncode==0 and not re.search(r"\b(sorry|admit)\b",output,re.I) and not missing and not bad
        result=dict(base,status="accepted" if passed else "rejected",lean_exit_code=p.returncode,axioms=reports,missing_axiom_reports=missing,disallowed_axioms=bad,output=output)
    except subprocess.TimeoutExpired as exc:
        partial=(exc.stdout or b'')
        if isinstance(partial,bytes): partial=partial.decode('utf-8',errors='replace')
        result=dict(base,status="timeout",output="Lean subprocess timed out\n"+partial)
    result.update(event="completed",time_utc=now(),elapsed_seconds=round(time.monotonic()-start,3))
    log(result)
    writej(path.with_suffix(".result.json"),result)
    print(json.dumps({k:v for k,v in result.items() if k not in ("output","axioms")}),flush=True)
    return result

def init():
    (ROOT/"snapshots").mkdir(exist_ok=True); (ROOT/"attempts").mkdir(exist_ok=True)
    if not CANON.is_file(): raise SystemExit(f"Missing canonical source: {CANON}")
    raw = CANON.read_bytes(); snap = ROOT/"snapshots"/"RankedCoalescenceSound.lean"
    if snap.exists() and snap.read_bytes() != raw: raise SystemExit("Snapshot already exists with different bytes; refusing replacement")
    if not snap.exists(): snap.write_bytes(raw)
    if not (ROOT/"manifest.json").exists():
        writej(ROOT/"manifest.json", {"canonical_path": str(CANON), "snapshot": "snapshots/RankedCoalescenceSound.lean", "sha256": digest(raw), "theorems": THEOREMS, "statement_sha256": digest(json.dumps(THEOREMS,sort_keys=True,ensure_ascii=False).encode()), "dependencies": DEPENDENCIES, "created_utc": now()})
    if not (ROOT/"state.json").exists(): writej(ROOT/"state.json", {"checks_used": 0, "check_limit": LIMIT, "initialized_utc": now()})
    if not (ROOT/"attempts.jsonl").exists(): (ROOT/"attempts.jsonl").write_text("", encoding="utf-8")
    print("Initialized; snapshot SHA256:", digest(raw))

def status():
    s=readj(ROOT/"state.json", {"checks_used":0}); print(json.dumps({"checks_used":s["checks_used"],"checks_remaining":max(0,LIMIT-s["checks_used"]),"stop_requested":(ROOT/"STOP").exists(),"accepted":readj(ROOT/"accepted.json",{})},indent=2))

def candidate_ok(body):
    if not isinstance(body,str) or not body.startswith("by\n") or FORBIDDEN.search(body): return False, "forbidden token or missing by block"
    for line in body.splitlines():
        if not line.strip(): continue
        if not line.strip().startswith(TACTICS): return False, "line outside restricted tactic grammar: "+line.strip()
    return True, ""

def module_for(name, body, accepted):
    src=(ROOT/"snapshots"/"RankedCoalescenceSound.lean").read_text(encoding="utf-8")
    bits=[src, "\nnamespace JevPilot\nopen NewMathDiscovery.RankedCoalescence\n"]
    for theorem in THEOREMS:
        proof = body if theorem == name else accepted.get(theorem)
        if proof is None: continue
        bits.append(f"theorem {theorem} {THEOREMS[theorem]} := {proof}\n")
    bits.append("end JevPilot\n")
    for theorem in THEOREMS:
        if theorem == name or theorem in accepted: bits.append(f"#print axioms JevPilot.{theorem}\n")
    return "".join(bits)

def run(baseline=False):
    started=time.monotonic(); manifest=readj(ROOT/"manifest.json",None)
    if not manifest: raise SystemExit("Run init first")
    if manifest.get("theorems") != THEOREMS or manifest.get("statement_sha256") != digest(json.dumps(THEOREMS,sort_keys=True,ensure_ascii=False).encode()): raise SystemExit("Frozen theorem statement manifest mismatch")
    snap=ROOT/manifest["snapshot"]
    if digest(snap.read_bytes()) != manifest["sha256"]: raise SystemExit("Snapshot hash mismatch")
    if not LEAN.is_file(): raise SystemExit(f"Lean executable unavailable: {LEAN}")
    proposals=readj(ROOT/"proposals.json",{}); accepted=readj(ROOT/"accepted.json",{})
    state=state_now()
    queue=[n for n in THEOREMS if n not in accepted]
    while queue:
        if (ROOT/"STOP").exists() or time.monotonic()-started >= RUN_SECONDS: break
        name=queue[0]
        if any(dep not in accepted for dep in DEPENDENCIES[name]):
            queue.append(queue.pop(0));
            if all(any(d not in accepted for d in DEPENDENCIES[x]) for x in queue): break
            continue
        body="by\n  simp_all [Reaches, iterate]" if baseline else proposals.get(name)
        ok, why=candidate_ok(body)
        record={"time_utc":now(),"theorem":name,"body":body,"status":"rejected","reason":why,"elapsed_seconds":0}
        if ok:
            if state["checks_used"] >= LIMIT: record["reason"]="global Lean check limit reached"
            else:
                record=check(module_for(name,body,accepted),("baseline-" if baseline else "luna-")+name,["JevPilot."+n for n in THEOREMS if n==name or n in accepted],min(CALL_SECONDS,max(0.1,RUN_SECONDS-(time.monotonic()-started))))
                state=state_now()
                if record["status"]=="accepted":
                    accepted[name]=body; writej(ROOT/"accepted.json",accepted)
                    (ROOT/"Accepted.lean").write_text(module_for(name,body,accepted),encoding="utf-8")
        if not ok: log(record)
        queue.pop(0)
    print(json.dumps({"checks_used":state["checks_used"],"checks_remaining":max(0,LIMIT-state["checks_used"]),"accepted":readj(ROOT/"accepted.json",{}),"paused":bool(queue)},indent=2))

def fixtures():
    # At most two Lean subprocesses. The negative proposition must fail.
    if not (ROOT/"manifest.json").exists(): init()
    assert axiom_reports("'target' does not depend on any axioms",["target"])[1:]==([], {})
    assert axiom_reports("'unrelated' depends on axioms: [propext]",["target"])[1]==["target"]
    assert axiom_reports("'target' depends on axioms: [sorryAx]",["target"])[2]
    for bad in ("by\n sorry", "by\n admit", "by\n native_decide", "by\n exact True.intro\naxiom bad : False", "by\n run_tac foo"):
        assert not candidate_ok(bad)[0]
    outcomes=[]
    for idx,(decl, expected) in enumerate([
      ("theorem fixture : True := by\n  trivial\n#print axioms fixture\n",True),
      ("theorem fixture : False := by\n  trivial\n#print axioms fixture\n",False)]):
        result=check(decl,"fixture-"+str(idx+1),["fixture"])
        matched=(result["status"]=="accepted")==expected
        outcomes.append(dict(expected_acceptance=expected,matched=matched,check_id=result["id"]))
    writej(ROOT/"fixture-results.json",dict(guards_passed=True,lean_fixtures=outcomes))
    if not all(x["matched"] for x in outcomes): raise SystemExit("Acceptance fixture failure")

def check_module():
    manifest=readj(ROOT/"module-manifest.json",None)
    if not manifest: raise SystemExit("Missing parent-reviewed module manifest")
    p=ROOT/manifest["path"]
    if digest(p.read_bytes())!=manifest["sha256"]: raise SystemExit("Reviewed module hash mismatch")
    previous=readj(ROOT/"boundary-result.json",{})
    if previous.get("status")=="accepted" and previous.get("source_sha256")==digest(p.read_text(encoding="utf-8").encode()) and previous.get("endpoints")==manifest["endpoints"]:
        print("Boundary module unchanged; using completed Lean receipt."); return
    result=check(p.read_text(encoding="utf-8"),"boundary",manifest["endpoints"])
    writej(ROOT/"boundary-result.json",result)
    if result["status"]!="accepted": raise SystemExit(1)

def main():
    p=argparse.ArgumentParser(); p.add_argument("command",choices=["init","run","baseline","status","check-fixtures","check-module"]); a=p.parse_args()
    if a.command=="status": status(); return
    with lock():
        {"init":init,"run":run,"baseline":lambda:run(True),"check-fixtures":fixtures,"check-module":check_module}[a.command]()
if __name__=="__main__": main()

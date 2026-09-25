"""Read-only setup checks. Never invokes a model, starts a loop or runs Lean."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

ROOT = Path(__file__).resolve().parent


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def probe(executable, args, timeout=40):
    if not executable.is_file():
        return {"status": "missing_interpreter", "path": str(executable)}
    try:
        run = subprocess.run([str(executable), *args], cwd=ROOT, shell=False,
                             capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if run.returncode != 0:
            return {"status": "failed", "exit_code": run.returncode,
                    "detail": "Run the component's documented import check for details."}
        return {"status": "verified", "result": json.loads(run.stdout)}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "error_type": type(exc).__name__}


def model_files(model_root, deep):
    result = []
    for folder in ("laya-55cf4c4e", "laya-typed-1a793eb5"):
        directory = model_root / folder
        receipt = read_json(directory / "download-receipt.json")
        if receipt is None:
            result.append({"folder": folder, "status": "missing_receipt"})
            continue
        files = []
        for entry in receipt["files"]:
            path = directory / entry["path"]
            row = {"path": entry["path"], "present": path.is_file()}
            if path.is_file():
                row["size_matches"] = path.stat().st_size == entry["bytes"]
                if deep:
                    with path.open("rb") as handle:
                        digest = hashlib.file_digest(handle, "sha256").hexdigest()
                    row["sha256_matches"] = digest == entry["sha256"]
            files.append(row)
        matches = all(r.get("present") and r.get("size_matches") and
                      (not deep or r.get("sha256_matches")) for r in files)
        result.append({"folder": folder, "revision": receipt["revision"],
                       "status": "verified" if matches else "failed", "deep_hashes": deep, "files": files})
    return result


def inspect(online=False, deep=False):
    runtime = read_json(ROOT / "runtime-config.local.json", {})
    loop_runtime = read_json(ROOT / "loop-runtime.local.json", {})
    components = read_json(ROOT / "components-runtime.local.json", {})
    report = {"checked_utc": datetime.now(timezone.utc).isoformat(),
              "model_calls": 0, "lean_subprocess_checks": 0,
              "lean_budget": read_json(ROOT / "state.json"),
              "legacy_luna_budget": read_json(ROOT / "agent-ledger.json"),
              "additional_luna_authorization": read_json(ROOT / "loop-authorization.json"),
              "active_component_runtimes": components,
              "stop_file": (ROOT / "STOP").exists(), "runner_lock_exists": (ROOT / "runner.lock").exists()}
    report["adk"] = probe(Path(components.get("adk_python", str(ROOT / ".venv-adk/Scripts/python.exe"))), ["-c",
        "import json,importlib.metadata as m; from google.adk import Workflow,Runner; "
        "print(json.dumps({'google_adk':m.version('google-adk'),'workflow_import':True}))"])
    report["typesafe"] = probe(Path(components.get("typesafe_python", str(ROOT / ".venv-typesafe/Scripts/python.exe"))),
        ["typesafe_readiness.py", "--self-test"] + (["--online"] if online else []))
    report["laya_runtime"] = probe(Path(runtime.get("python", "")), ["-c",
        "import json,sys; from pathlib import Path; sys.path.insert(0,str(Path.cwd()/'runtime/laya-source')); "
        "import laya,torch,transformers; "
        "print(json.dumps({'laya_import':True,'torch':torch.__version__,'transformers':transformers.__version__,"
        "'cuda_available':torch.cuda.is_available(),'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"])
    report["checkpoints"] = model_files(Path(runtime.get("model_root", "__unconfigured__")), deep)
    codex = Path(loop_runtime.get("codex_executable", ""))
    report["codex_executable_present"] = codex.is_file()
    database = ROOT / "loop-state.sqlite3"
    if database.is_file():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            jobs = [json.loads(r[0]) for r in connection.execute("SELECT data FROM jobs")]
            report["jobs"] = [{"id":j["id"], "phase":j["phase"], "attempt":j["attempt"], "mode":j["mode"]} for j in jobs]
            report["additional_luna_reserved"] = connection.execute("SELECT count(*) FROM proposals WHERE mode='codex'").fetchone()[0]
            report["database_integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
    else:
        report["database_integrity"] = "missing_do_not_recreate_spending_ledger"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="Allow one metadata GET if a TypeSafe key is available")
    parser.add_argument("--deep-hashes", action="store_true", help="Read and hash checkpoint files; no inference")
    parser.add_argument("--output", type=Path, help="Optionally write a JSON receipt")
    args = parser.parse_args()
    result = inspect(args.online, args.deep_hashes)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

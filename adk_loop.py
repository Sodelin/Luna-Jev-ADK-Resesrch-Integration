"""Google ADK 2.6 workflow adapter; no Google model or cloud account required."""
import argparse
import asyncio
import json
import time

from google.adk import Context, Runner, Workflow
from google.adk.agents.run_config import RunConfig
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import node
from google.genai import types

from luna_loop import ROOT, Store, Worker, MAX_ATTEMPTS, JOB_SECONDS, TERMINAL, process_lock


async def run_adk(job_id, max_steps=MAX_ATTEMPTS):
    store = Store()
    worker = Worker(store)
    state = worker.check_resume(job_id)
    # Completed jobs exit before ADK creates a session or any further tool call.
    if state["phase"] in TERMINAL:
        store.close()
        return state

    @node(name="propose_proof")
    def propose_proof(node_input: str):
        return worker.propose(node_input)

    @node(name="verify_with_lean")
    def verify_with_lean(node_input: str):
        return worker.verify(node_input)

    @node(name="bounded_proof_loop", rerun_on_resume=True)
    async def bounded_proof_loop(ctx: Context):
        state = worker.status(job_id)
        deadline = time.monotonic() + JOB_SECONDS
        for step in range(max_steps):
            if state["phase"] in TERMINAL or time.monotonic() >= deadline:
                break
            if state["phase"] == "ready":
                state = await ctx.run_node(propose_proof, job_id, run_id=f"proposal_{step}")
            if state["phase"] == "candidate":
                state = await ctx.run_node(verify_with_lean, job_id, run_id=f"check_{step}")
        return state

    workflow = Workflow(name="luna_lean_loop", edges=[("START", bounded_proof_loop)], max_concurrency=1)
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="luna_lean_loop", user_id="local", session_id=job_id)
    executor = Runner(node=workflow, app_name="luna_lean_loop", session_service=sessions)
    # ADK session history is ephemeral. SQLite plus hashed files is the durable
    # authority, including across fresh ADK invocations and process restarts.
    event_path = ROOT / "loop-runs" / job_id / ("adk-" + str(time.time_ns()) + ".jsonl")
    event_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with event_path.open("w", encoding="utf-8") as handle:
            async for event in executor.run_async(
                user_id="local", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=job_id)]),
                run_config=RunConfig(max_llm_calls=1),
            ):
                handle.write(event.model_dump_json(exclude_none=True) + "\n")
                handle.flush()
        return worker.status(job_id)
    finally:
        await executor.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--max-steps", type=int, choices=range(1, MAX_ATTEMPTS + 1), default=MAX_ATTEMPTS)
    args = parser.parse_args()
    with process_lock():
        result = asyncio.run(run_adk(args.job_id, args.max_steps))
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

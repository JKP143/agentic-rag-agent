#!/usr/bin/env python3
"""
Fetch an n8n execution's full data and summarize it.

Usage:
    python tools/fetch_execution.py <execution_id>
    python tools/fetch_execution.py --workflow <workflow_id> --latest
    python tools/fetch_execution.py <execution_id> --save

Summarizes status, duration, node run counts, and the error node/message if
the run failed. --save writes the full JSON to .tmp/exec_<id>.json so you can
grep/jq through it for deeper inspection.

WHEN TO USE: any time a user reports a workflow failure. Read the actual data
before proposing a fix — guessing at node logic wastes round-trips when the
real bug is in the data shape at some earlier node.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line, re.I)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip('"\'')


def api(path):
    base = os.environ["N8N_BASE_URL"].rstrip("/")
    headers = {"X-N8N-API-KEY": os.environ["N8N_API_KEY"], "Accept": "application/json"}
    req = Request(base + path, headers=headers)
    try:
        with urlopen(req) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body_text)
        except json.JSONDecodeError:
            return e.code, body_text


def main():
    load_env()
    p = argparse.ArgumentParser(description="Fetch and summarize an n8n execution.")
    p.add_argument("execution_id", nargs="?", help="Execution ID (omit if using --latest)")
    p.add_argument("--workflow", help="Workflow ID, required with --latest")
    p.add_argument("--latest", action="store_true", help="Fetch the most recent execution of --workflow")
    p.add_argument("--save", action="store_true", help="Dump full JSON to .tmp/exec_<id>.json")
    args = p.parse_args()

    exec_id = args.execution_id
    if not exec_id:
        if not (args.workflow and args.latest):
            p.error("Provide <execution_id> OR --workflow <id> --latest")
        code, data = api(f"/api/v1/executions?workflowId={args.workflow}&limit=1")
        if code != 200:
            sys.exit(f"HTTP {code}: {data}")
        if not data.get("data"):
            sys.exit("No executions found for that workflow")
        exec_id = data["data"][0]["id"]

    code, j = api(f"/api/v1/executions/{exec_id}?includeData=true")
    if code != 200:
        sys.exit(f"HTTP {code}: {j}")

    print(
        f"Execution {exec_id}: status={j.get('status')} finished={j.get('finished')} "
        f"startedAt={j.get('startedAt')} stoppedAt={j.get('stoppedAt')}"
    )

    result_data = (j.get("data") or {}).get("resultData") or {}
    run = result_data.get("runData") or {}
    error = result_data.get("error")

    if run:
        print("\nNode run counts:")
        for name, runs in run.items():
            err_count = sum(1 for r in runs if r.get("error"))
            marker = f"  [{err_count} error]" if err_count else ""
            print(f"  {name}: {len(runs)}{marker}")

    if error:
        node_name = (error.get("node") or {}).get("name", "?")
        print(f"\nERROR at node {node_name!r}:")
        print(f"  message: {error.get('message')}")
        if error.get("description"):
            print(f"  details: {error['description']}")

    if args.save:
        out = Path(__file__).resolve().parent.parent / ".tmp" / f"exec_{exec_id}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(j, indent=2), encoding="utf-8")
        try:
            rel = out.relative_to(Path.cwd())
        except ValueError:
            rel = out
        print(f"\nFull data saved to {rel}")


if __name__ == "__main__":
    main()

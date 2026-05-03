#!/usr/bin/env python3
"""
Sync a local workflow JSON file to the running n8n via REST API.

Usage:
    python tools/sync_workflow.py <workflow.json> [workflow_id]

If workflow_id is omitted, looks for N8N_<NAME>_WORKFLOW_ID in .env matching
the filename (e.g. video-analysis-agent.n8n.json -> N8N_VIDEO_ANALYSIS_WORKFLOW_ID;
the tokens AGENT and N8N are dropped from the filename stem when matching).

Reads N8N_BASE_URL and N8N_API_KEY from .env. Does a PUT /api/v1/workflows/{id}
preserving live settings (filters out UI-only fields the public API rejects with
"settings must NOT have additional properties"). Credential bindings + webhook
IDs survive in place because the workflow keeps its ID.
"""
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


def api(method, path, body=None):
    base = os.environ["N8N_BASE_URL"].rstrip("/")
    headers = {"X-N8N-API-KEY": os.environ["N8N_API_KEY"], "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = Request(base + path, data=data, method=method, headers=headers)
    try:
        with urlopen(req) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body_text)
        except json.JSONDecodeError:
            return e.code, body_text


def resolve_workflow_id(wf_path: Path) -> str | None:
    stem = wf_path.stem.replace(".n8n", "")
    tokens = [t for t in stem.upper().split("-") if t not in ("AGENT", "N8N")]
    guess = f"N8N_{'_'.join(tokens)}_WORKFLOW_ID"
    if guess in os.environ:
        return os.environ[guess]
    return None


def main():
    load_env()
    if len(sys.argv) < 2:
        sys.exit("Usage: python tools/sync_workflow.py <workflow.json> [workflow_id]")

    wf_path = Path(sys.argv[1])
    if not wf_path.exists():
        sys.exit(f"File not found: {wf_path}")

    local = json.loads(wf_path.read_text(encoding="utf-8"))

    wf_id = sys.argv[2] if len(sys.argv) > 2 else resolve_workflow_id(wf_path)
    if not wf_id:
        sys.exit(
            "No workflow ID given and no matching N8N_<NAME>_WORKFLOW_ID in .env.\n"
            f"Pass the ID as the second argument: python tools/sync_workflow.py {wf_path} <id>"
        )

    if not os.environ.get("N8N_BASE_URL") or not os.environ.get("N8N_API_KEY"):
        sys.exit("N8N_BASE_URL and N8N_API_KEY must be set in .env")

    code, live = api("GET", f"/api/v1/workflows/{wf_id}")
    if code != 200:
        sys.exit(f"GET failed: HTTP {code}\n{live}")

    allowed_settings = {
        "saveExecutionProgress",
        "saveManualExecutions",
        "saveDataErrorExecution",
        "saveDataSuccessExecution",
        "executionTimeout",
        "errorWorkflow",
        "timezone",
        "executionOrder",
    }
    live_settings = live.get("settings") or {}
    settings = {k: live_settings[k] for k in allowed_settings if k in live_settings}
    settings.setdefault("executionOrder", "v1")

    payload = {
        "name": local.get("name") or live.get("name"),
        "nodes": local["nodes"],
        "connections": local["connections"],
        "settings": settings,
    }
    if "staticData" in local:
        payload["staticData"] = local["staticData"]

    code, resp = api("PUT", f"/api/v1/workflows/{wf_id}", payload)
    if code < 200 or code >= 300:
        sys.exit(f"PUT failed: HTTP {code}\n{resp}")

    print(
        f'Synced: "{resp["name"]}" ({wf_id}) — '
        f'{len(resp["nodes"])} nodes, active={resp.get("active")}'
    )
    live_names = {n["name"] for n in live["nodes"]}
    local_names = {n["name"] for n in local["nodes"]}
    added = sorted(local_names - live_names)
    removed = sorted(live_names - local_names)
    if added:
        print("  + added:", ", ".join(added))
    if removed:
        print("  - removed:", ", ".join(removed))
    if not added and not removed:
        print("  (node set unchanged — parameter-only edits)")
    print("\nDone. Refresh the n8n canvas in your browser to see updates.")


if __name__ == "__main__":
    main()

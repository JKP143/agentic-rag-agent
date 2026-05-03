#!/usr/bin/env python3
"""
List recent n8n executions for a workflow.

Usage:
    python tools/list_executions.py [--workflow <id>] [--limit 10] [--status <s>]

If --workflow is omitted, falls back to $N8N_VIDEO_ANALYSIS_WORKFLOW_ID.
--status filters by "success", "error", "running", etc. Prints id, status,
duration, and start time in a compact table.

WHEN TO USE: quick "did my workflow run?" check without opening n8n UI.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
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


def fmt_duration(started, stopped):
    if not started or not stopped:
        return "-"
    try:
        a = datetime.fromisoformat(started.replace("Z", "+00:00"))
        b = datetime.fromisoformat(stopped.replace("Z", "+00:00"))
        d = (b - a).total_seconds()
        return f"{d:.1f}s" if d < 60 else f"{d / 60:.1f}m"
    except Exception:
        return "-"


def main():
    load_env()
    p = argparse.ArgumentParser(description="List recent n8n executions for a workflow.")
    p.add_argument("--workflow", default=os.environ.get("N8N_VIDEO_ANALYSIS_WORKFLOW_ID"))
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--status", help="Filter: success / error / running / waiting / canceled")
    args = p.parse_args()

    if not args.workflow:
        sys.exit("No --workflow given and N8N_VIDEO_ANALYSIS_WORKFLOW_ID is not set in .env")

    params = {"workflowId": args.workflow, "limit": args.limit}
    if args.status:
        params["status"] = args.status
    code, j = api("/api/v1/executions?" + urlencode(params))
    if code != 200:
        sys.exit(f"HTTP {code}: {j}")

    data = j.get("data") or []
    if not data:
        print("No executions found.")
        return

    print(f"{'ID':>6}  {'STATUS':<9}  {'DURATION':>8}  STARTED")
    print("-" * 60)
    for e in data:
        print(
            f"{e.get('id', '-'):>6}  "
            f"{e.get('status', '-'):<9}  "
            f"{fmt_duration(e.get('startedAt'), e.get('stoppedAt')):>8}  "
            f"{e.get('startedAt', '-')}"
        )


if __name__ == "__main__":
    main()

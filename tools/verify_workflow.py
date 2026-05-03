#!/usr/bin/env python3
"""
Static verification of an n8n workflow JSON file.

Usage:
    python tools/verify_workflow.py <workflow.json>

Catches the common pre-deploy breakage modes:
- Invalid JSON
- Missing top-level `nodes` list or `connections` dict
- Nodes missing id / name / type
- Duplicate node names (n8n re-import rename quirk becomes fatal here)
- Connections referencing a source or target node that does not exist
- Node parameters that are not a dict (n8n rejects null / list)

Exit code 0 if all checks pass, 1 if any fail. Run this before `sync_workflow.py`.
Project-specific checks (expression-JS parsing, jsCode dry-runs) live in
.tmp/verify_<name>_workflow.js — this file is just the generic layer.
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python tools/verify_workflow.py <workflow.json>")

    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"Not found: {path}")

    text = path.read_text(encoding="utf-8")
    try:
        wf = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[-] JSON parse — FAIL: line {e.lineno} col {e.colno}: {e.msg}")
        sys.exit(1)

    passes = 0
    fails = 0

    def check(label, ok, detail=""):
        nonlocal passes, fails
        if ok:
            passes += 1
            print(f"[+] {label}")
        else:
            fails += 1
            print(f"[-] {label} — FAIL" + (f" ({detail})" if detail else ""))

    check("JSON parses", True)

    nodes = wf.get("nodes")
    check("top-level has `nodes` list", isinstance(nodes, list), f"got {type(nodes).__name__}")
    conns = wf.get("connections")
    check("top-level has `connections` dict", isinstance(conns, dict), f"got {type(conns).__name__}")

    if not isinstance(nodes, list) or not isinstance(conns, dict):
        print(f"\nTotal: {passes} passed, {fails} failed")
        sys.exit(1)

    names = [n.get("name") for n in nodes]
    dupes = sorted({n for n in names if names.count(n) > 1 and n is not None})
    check("node names are unique", not dupes, f"duplicates: {dupes}")

    for i, n in enumerate(nodes):
        label = repr(n.get("name", f"[{i}]"))
        has_core = bool(n.get("id")) and bool(n.get("name")) and bool(n.get("type"))
        check(f"node {label} has id/name/type", has_core)
        params = n.get("parameters")
        ok = params is None or isinstance(params, dict)
        check(f"node {label} parameters is dict or absent", ok, f"got {type(params).__name__}")

    node_names = set(names)
    for src, outs in conns.items():
        check(f"connection source {src!r} exists as a node", src in node_names)
        main_outs = (outs or {}).get("main") or []
        for out_idx, outputs in enumerate(main_outs):
            for edge in outputs or []:
                tgt = edge.get("node")
                check(f"edge {src!r}[{out_idx}] -> {tgt!r} target exists", tgt in node_names)

    print(f"\nTotal: {passes} passed, {fails} failed")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()

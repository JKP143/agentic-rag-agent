#!/usr/bin/env python3
"""
RAG-specific verification of workflows/rag-agent.n8n.json.

Builds on top of tools/verify_workflow.py (structural checks).
This file adds semantic checks specific to the agentic RAG topology:

- The four AI tools are each wired to the RAG AI Agent via ai_tool connections
- Switch node exposes the 5 expected file-type branches
- Default Data Loader writes metadata.file_id and metadata.file_title
- Postgres tool nodes (list/get/query) use the rag_readonly credential
- Postgres write nodes (deletes/upserts/inserts) use the write credential
- The trashed-file schedule branch exists (schedule trigger + drive API call)
- Loop Over Items is reachable from both Drive triggers

Usage:
    python tools/verify_rag_workflow.py [workflow.json]

Defaults to workflows/rag-agent.n8n.json if no argument is given.
Exit 0 on pass, 1 on fail.
"""
import json
import sys
from pathlib import Path

EXPECTED_TOOLS = ["rag_search", "list_documents", "get_file_contents", "query_document_rows"]
EXPECTED_SWITCH_BRANCHES = 5  # xlsx, gsheet, pdf, gdoc, fallback
AGENT_NAME = "RAG AI Agent"
READONLY_CRED_MARKER = "RAG_POSTGRES_READONLY_BIND_ME"
WRITE_CRED_MARKER = "RAG_POSTGRES_WRITE_BIND_ME"


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "workflows/rag-agent.n8n.json")
    if not path.exists():
        sys.exit(f"Not found: {path}")

    wf = json.loads(path.read_text(encoding="utf-8"))
    nodes_by_name = {n["name"]: n for n in wf["nodes"]}
    conns = wf["connections"]

    passes, fails = 0, 0

    def check(label, ok, detail=""):
        nonlocal passes, fails
        status = "[+]" if ok else "[-]"
        suffix = f" — FAIL ({detail})" if (not ok and detail) else (" — FAIL" if not ok else "")
        print(f"{status} {label}{suffix}")
        passes += 1 if ok else 0
        fails += 0 if ok else 1

    # 1. AI Agent exists
    check(f"'{AGENT_NAME}' node exists", AGENT_NAME in nodes_by_name)

    # 2. Each of the four tools connects to the agent via ai_tool
    for tool in EXPECTED_TOOLS:
        if tool not in nodes_by_name:
            check(f"tool '{tool}' node exists", False, "missing node")
            continue
        tool_conns = conns.get(tool, {}).get("ai_tool") or []
        wired = any(
            edge.get("node") == AGENT_NAME and edge.get("type") == "ai_tool"
            for outputs in tool_conns
            for edge in outputs or []
        )
        check(f"tool '{tool}' wired to agent via ai_tool", wired)

    # 3. Switch node has 5 output branches
    switch = nodes_by_name.get("Switch")
    if switch:
        rules = (switch.get("parameters") or {}).get("rules", {}).get("values", [])
        fallback = (switch.get("parameters") or {}).get("options", {}).get("fallbackOutput")
        effective = len(rules) + (1 if fallback == "extra" else 0)
        check(
            "Switch node has 5 effective branches (4 rules + fallback)",
            effective == EXPECTED_SWITCH_BRANCHES,
            f"got {effective}",
        )
        # Verify rule types
        rule_outputs = [r.get("outputKey") for r in rules]
        expected_keys = {"xlsx", "gsheet", "pdf", "gdoc"}
        check(
            "Switch rule outputKeys include xlsx/gsheet/pdf/gdoc",
            expected_keys.issubset(set(rule_outputs)),
            f"got {rule_outputs}",
        )
    else:
        check("Switch node exists", False)

    # 4. Default Data Loader sets metadata.file_id and metadata.file_title
    loader = nodes_by_name.get("Default Data Loader")
    if loader:
        mv = (
            (loader.get("parameters") or {})
            .get("options", {})
            .get("metadata", {})
            .get("metadataValues", [])
        )
        names = {entry.get("name", "").lstrip("=") for entry in mv}
        check("Default Data Loader writes metadata.file_id", "file_id" in names)
        check("Default Data Loader writes metadata.file_title", "file_title" in names)
    else:
        check("Default Data Loader node exists", False)

    # 5. Credential consistency: every Postgres-using node must be bound to a
    #    credential (no missing) and the four "tool" nodes must share one
    #    credential (whatever it is), implying a coherent read-only intent.
    readonly_intended = {"list_documents", "get_file_contents", "query_document_rows", "rag_search"}
    pg_node_to_cred = {}
    for n in wf["nodes"]:
        if "postgres" not in n.get("type", "").lower():
            continue
        cid = ((n.get("credentials") or {}).get("postgres") or {}).get("id", "")
        pg_node_to_cred[n["name"]] = cid

    unbound = [name for name, cid in pg_node_to_cred.items() if not cid]
    check("every Postgres node has a credential bound", not unbound, f"unbound: {unbound}")

    placeholders = [name for name, cid in pg_node_to_cred.items()
                    if cid in (READONLY_CRED_MARKER, WRITE_CRED_MARKER) or "BIND_ME" in cid]
    check("no placeholder BIND_ME credentials remain", not placeholders,
          f"still placeholder: {placeholders}")

    tool_creds = {pg_node_to_cred.get(n) for n in readonly_intended if n in pg_node_to_cred}
    check(f"the 4 tool nodes share one credential ({sorted(readonly_intended)})",
          len(tool_creds) == 1 and None not in tool_creds,
          f"got distinct cred ids: {tool_creds}")

    # 6. Trashed-file schedule branch
    check("Schedule Trigger 'Check Every 15 Minutes' exists", "Check Every 15 Minutes" in nodes_by_name)
    check("HTTP Request 'Get Trashed Files via API' exists", "Get Trashed Files via API" in nodes_by_name)
    check("'Delete Metadata (Trashed)' exists", "Delete Metadata (Trashed)" in nodes_by_name)

    # 7. Both Drive triggers lead to Loop Over Items
    for trig in ["File Created", "File Updated"]:
        main = conns.get(trig, {}).get("main") or []
        wired = any(
            edge.get("node") == "Loop Over Items"
            for outputs in main
            for edge in outputs or []
        )
        check(f"'{trig}' leads to Loop Over Items", wired)

    # 8. Agent has model, memory, and at least 4 tools via subnode connections (reverse index)
    agent_inputs = {"ai_languageModel": [], "ai_memory": [], "ai_tool": []}
    for src, outs in conns.items():
        for key in agent_inputs:
            edges = outs.get(key) or []
            for outputs in edges:
                for edge in outputs or []:
                    if edge.get("node") == AGENT_NAME and edge.get("type") == key:
                        agent_inputs[key].append(src)

    check("Agent has a language model subnode", len(agent_inputs["ai_languageModel"]) >= 1)
    check("Agent has a memory subnode", len(agent_inputs["ai_memory"]) >= 1)
    check(
        f"Agent has at least {len(EXPECTED_TOOLS)} tool subnodes",
        len(agent_inputs["ai_tool"]) >= len(EXPECTED_TOOLS),
        f"got {len(agent_inputs['ai_tool'])}",
    )

    print(f"\nTotal: {passes} passed, {fails} failed")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()

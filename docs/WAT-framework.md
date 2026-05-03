# The WAT Framework

This project is built on the **WAT** pattern — **Workflows, Agents, Tools** — a
lightweight architecture for combining LLM reasoning with deterministic
execution. The split is what makes the system reliable.

## Why three layers

When an LLM tries to do every step itself, accuracy compounds badly. Five steps
at 90% accuracy each is only 59% end-to-end. Pushing execution to deterministic
code keeps the LLM focused on what it does best — orchestration and judgment.

## Layer 1: Workflows (the instructions)

Markdown SOPs in `workflow/`. Each one defines:

- The objective.
- The inputs the workflow needs.
- Which tools to use and in what order.
- The expected output.
- How to handle the common edge cases.

Workflows are written in plain English. If a teammate could follow it, the
agent can too.

## Layer 2: Agents (the decision-maker)

The LLM (e.g. Claude, GPT, Gemini) reads the workflow and orchestrates
execution. It picks the right tool for each step, handles failures gracefully,
and asks for clarification when inputs are ambiguous.

It does **not** try to do the work directly. If you need to scrape a page, it
calls a script that scrapes the page — it does not "imagine" the HTML.

## Layer 3: Tools (the execution)

Standalone Python scripts in `tools/`. Each script:

- Does one thing (an API call, a transformation, a DB query).
- Runs in isolation as `python tools/<name>.py`.
- Reads credentials from `.env` (never hard-coded, never committed).
- Has no shared build/test harness — if it needs deps, it manages its own
  `venv/`.

This makes tools easy to test, easy to swap, and obvious to debug.

## The self-improvement loop

Every failure is an opportunity to make the system more reliable:

1. Identify what broke.
2. Fix the tool.
3. Verify the fix.
4. Update the workflow with the new constraint or technique.
5. Move on with a sturdier system.

Over time the workflow becomes the institutional memory of the project.

## File layout

```
workflow/         Markdown SOP + n8n workflow JSON
tools/            Python scripts, each runnable standalone
sql/              Database schema (when applicable)
docs/             Supporting documentation
.env              Secrets — gitignored, never committed
```

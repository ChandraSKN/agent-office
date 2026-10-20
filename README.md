# Agent Office

Chandra's AI office: a **CTO** that plans goals and hands the work to 19 specialists: a software team, a hardware lab, product & research, and operations (an Operations Manager and a parts sourcer). Buddy is the CEO. They do real work (code, firmware, BOMs, CAD, test plans) in a project folder.

```bash
uv run office.py                                           # then open http://localhost:8765
uv run office.py "add a temp sensor to Buddy" --project ../desktop-buddy
uv run office.py "..." --once                              # one goal, then exit (scripts)
```

**3D office** (`viewer/index.html`, three.js): every agent has a desk. Agents bounce with a green ring while working; a yellow ring and "needs your OK" mean waiting. Envelopes fly between desks on hand-offs. The top bar shows live counts, and the execution log can be filtered by team. Click an agent for its card and recent activity, and to message it (through the CTO). Risky actions pop up an **Approve / Deny** card. You can type goals and answers in the terminal or the browser.

- **Roster:** `roster.py` (edit a role's tools or instructions there)
- **Live log:** printed in the terminal + `state/events.jsonl` (GOAL / SEND / START / STEP / ASK / APPROVE / DENY / DONE)
- **Desk status:** `state/board.json`, one entry per agent (idle / working / waiting, current task, tasks done)
- **Approvals:** reads, web searches and edits inside the project folder run freely. Any other shell command (installing, flashing, pushing, `rm`, redirects) and any write outside the project folder stops and asks you `[y/N]`.
- **Default project folder:** `workspace/`
- **Model:** Claude Opus 5 via your Claude Code login (no API key needed)

**Buddy** (desktop-buddy) is the voice front door: "Hey Buddy, ask the office to…" hands over a goal (and starts the office if it is off), "how is the office doing?" reads its status, and Buddy announces when a job finishes or an agent needs your OK (spoken + a desktop notification with Approve / Deny buttons). Started this way, output goes to `office.log`.

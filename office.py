"""The AI office: a CTO hands work to 19 specialist agents.

    uv run office.py                          # chat in the terminal, or in the 3D office at http://localhost:8765
    uv run office.py "add a temp sensor to Buddy" --project ../desktop-buddy
    uv run office.py "..." --once             # run one goal and exit (for scripts)

Everything that happens is printed live, appended to state/events.jsonl and
summarised per agent in state/board.json. Risky actions stop and ask you first,
in the terminal or in the browser.
"""
import argparse
import asyncio
import contextlib
import json
import re
import sys
import threading
import time
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from claude_agent_sdk import (AgentDefinition, AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                              HookMatcher, PermissionResultAllow, PermissionResultDeny, ResultMessage,
                              TextBlock)

from roster import HOUSE_RULES, ROLES

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
MANAGER = "cto"  # plans, delegates and runs quality gates, like a startup CTO
CEO = "buddy"  # Buddy, Chandra's desktop companion, runs the office as CEO
MODEL = "claude-opus-5"
PORT = 8765

# Bash commands that only read or build locally run without asking; everything else asks.
SAFE_BASH = re.compile(r"^\s*(ls|cat|head|tail|wc|pwd|echo|grep|rg|find|tree|file|which|"
                       r"git (status|diff|log|show)|python3? -m (pytest|py_compile)|pytest|uv run pytest|"
                       # hardware tools that only check designs or write files (flashing is not here)
                       r"pio (check|run)(?!.*(-t|--target)\s*\w*upload)|arduino-cli compile|openscad|ngspice -b|"
                       r"kicad-cli (sch|pcb) (export|erc|drc)|prusa-slicer (--export-gcode|-g|--info))\b")
CHAIN = re.compile(r"[;&|`$><]|\brm\b")  # chaining, redirects or rm make a "safe" command unsafe


def needs_approval(tool, inp, project):
    """True when a tool call should wait for Chandra's yes/no."""
    if tool == "Bash":
        cmd = inp.get("command", "")
        return not SAFE_BASH.match(cmd) or bool(CHAIN.search(cmd))
    if tool in ("Write", "Edit", "NotebookEdit"):
        path = Path(inp.get("file_path") or inp.get("notebook_path") or "").expanduser().resolve()
        return not path.is_relative_to(project)  # writing outside the project folder
    return False


UPLOAD_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md", ".csv"}
MAX_UPLOAD = 25 * 1024 * 1024


def upload_path(project, name):
    """Where an uploaded file goes: <project>/briefs/<date>/<clean name>, never overwriting.
    None for a type the team can't read."""
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if ext not in UPLOAD_TYPES:
        return None
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:60] or "file"
    folder = project / "briefs" / time.strftime("%Y-%m-%d")
    path, n = folder / f"{stem}{ext}", 2
    while path.exists():
        path, n = folder / f"{stem}-{n}{ext}", n + 1
    return path


def valid_project(path):
    """A goal's project folder: an existing directory inside the home folder (not home itself)."""
    p = Path(str(path)).expanduser().resolve()
    home = Path.home().resolve()
    return p if p.is_dir() and p != home and p.is_relative_to(home) else None


class Office:
    def __init__(self, project, can_ask):
        self.project = project
        self.can_ask = can_ask  # False in --once runs without a terminal: deny instead of waiting
        STATE.mkdir(exist_ok=True)
        self.events = STATE / "events.jsonl"
        self.board_path = STATE / "board.json"
        try:
            self.board = json.loads(self.board_path.read_text())
        except (OSError, ValueError):
            self.board = {}
        staff = {CEO: ("ceo",), MANAGER: ("cto",), **ROLES}
        self.board = {k: v for k, v in self.board.items() if k in staff}  # drop renamed roles
        for name, (team, *_rest) in staff.items():
            self.board.setdefault(name, {"team": team, "done": 0}).update(status="idle", task="")
        self.pending = {}  # Agent tool_use_id -> (role, task), until the sub-agent starts
        self.agent_ids = {}  # sub-agent id -> role
        self.recent = deque(maxlen=300)  # last events, for the viewer
        self.goals = asyncio.Queue()  # (text, who sent it: "chandra" or CEO), from the terminal, browser or Buddy
        self.asking = None  # {"agent","tool","what"} while an approval is open
        self.answer = None  # future the open approval waits on
        self.loop = asyncio.get_running_loop()
        self.save_board()

    # ── state files ──
    def log(self, agent, kind, text, **extra):
        ev = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": agent, "kind": kind, "text": text, **extra}
        self.recent.append(ev)
        with self.events.open("a") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        print(f"\033[2m{ev['ts'][11:]}\033[0m {agent:>18} │ {kind:<7} {text[:300]}", flush=True)

    def save_board(self):
        tmp = self.board_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.board, indent=1, ensure_ascii=False))
        tmp.replace(self.board_path)  # atomic, so readers never see half a file

    def set(self, agent, **kw):
        self.board[agent].update(kw)
        self.save_board()

    def busy(self):
        return any(v["status"] != "idle" for k, v in self.board.items() if k not in (MANAGER, CEO))

    def snapshot(self):  # called from the web thread; copies are cheap at this size
        return {"board": self.board, "events": list(self.recent), "asking": self.asking,
                "queued": self.goals.qsize(), "project": str(self.project)}

    @staticmethod
    def who(inp):
        return inp.get("agent_type") or MANAGER

    # ── input from the terminal and the browser (both arrive here on the event loop) ──
    def on_line(self, text):
        """A terminal line answers the open approval if there is one, otherwise it is a new goal."""
        if self.answer and not self.answer.done():
            self.answer.set_result(text.strip().lower() in ("y", "yes"))
        elif text.strip():
            self.goals.put_nowait((text.strip(), "chandra", None, None))

    def on_approve(self, ok):
        if self.answer and not self.answer.done():
            self.answer.set_result(ok)

    # ── hooks ──
    async def pre_tool(self, inp, tool_use_id, ctx):
        tool, args, agent = inp["tool_name"], inp["tool_input"], self.who(inp)
        if tool in ("Agent", "Task"):
            role = args.get("subagent_type", "?")
            task = args.get("description") or args.get("prompt", "")[:80]
            self.pending[tool_use_id] = (role, task)
            self.log(agent, "SEND", f"→ {role}: {task}")
        else:
            target = args.get("file_path") or args.get("command") or args.get("pattern") or args.get("query") or args.get("url") or ""
            self.log(agent, "STEP", f"{tool} {str(target)[:90]}")
        return {}

    async def subagent_start(self, inp, tool_use_id, ctx):
        role = inp["agent_type"]
        self.agent_ids[inp["agent_id"]] = role
        task = next((t for r, t in self.pending.values() if r == role), "")
        if role in self.board:
            self.set(role, status="working", task=task)
        self.log(role, "START", task)
        return {}

    async def subagent_stop(self, inp, tool_use_id, ctx):
        role = inp["agent_type"]
        if role in self.board:
            self.set(role, status="idle", task="", done=self.board[role]["done"] + 1)
        self.pending = {k: v for k, v in self.pending.items() if v[0] != role}
        self.log(role, "DONE", (inp.get("last_assistant_message") or "").strip()[:2000])
        return {}

    # ── approvals ──
    async def can_use_tool(self, tool, args, ctx):
        if not needs_approval(tool, args, self.project):
            return PermissionResultAllow()
        agent = self.agent_ids.get(ctx.agent_id, MANAGER) if ctx.agent_id else MANAGER
        what = args.get("command") or args.get("file_path") or json.dumps(args)[:200]
        self.set(agent, status="waiting")
        self.log(agent, "ASK", f"{tool}: {what}")
        if self.can_ask:
            self.asking = {"agent": agent, "tool": tool, "what": what}
            self.answer = self.loop.create_future()
            print(f"\a\033[1;33m  approve {agent} → {tool}: {what}?  [y/N] (or use the browser)\033[0m", flush=True)
            ok = await self.answer
            self.asking = self.answer = None
        else:
            ok = False  # nobody to ask, so deny
        self.set(agent, status="working" if agent != MANAGER else "idle")
        self.log(agent, "APPROVE" if ok else "DENY", f"{tool}: {what}")
        if ok:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Chandra said no. Pick a safer way or report what you need.")


# ── web server for the 3D office (localhost only) ──
def start_web(office):
    roster = {CEO: {"team": "ceo", "desc": "CEO. Chandra's desktop companion: takes goals by voice, hands them to the "
                                       "CTO, relays approvals and reports the results back to Chandra."},
              MANAGER: {"team": "cto", "desc": "CTO. Plans the goals, hands out tasks, runs the quality gates, reports back."}}
    roster |= {n: {"team": t, "desc": d} for n, (t, d, *_r) in ROLES.items()}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(HERE / "viewer"), **kw)

        def log_message(self, *a):  # keep the terminal for office events
            pass

        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/state":
                return self.send_json(office.snapshot())
            if self.path == "/api/roster":
                return self.send_json(roster)
            super().do_GET()

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            if self.path.startswith("/api/upload"):
                return self.upload(size)
            if size > 20_000:
                return self.send_json({"error": "too long"}, 413)
            try:
                data = json.loads(self.rfile.read(size) or b"{}")
            except ValueError:
                return self.send_json({"error": "bad json"}, 400)
            if self.path == "/api/message" and str(data.get("text", "")).strip():
                text, to = str(data["text"]).strip(), data.get("to")
                if to in roster and to != MANAGER:
                    text = f"(Chandra, about {to}) {text}"  # the manager routes it to that agent
                who = CEO if data.get("from") == CEO else "chandra"
                project = None
                if data.get("project"):
                    project = valid_project(data["project"])
                    if project is None:
                        return self.send_json({"error": "project must be a folder in your home directory"}, 400)
                files = [f for f in map(Path, data.get("attachments") or [])
                         if f.is_file() and f.resolve().is_relative_to(office.project / "briefs")]
                if files:
                    text += ("\n\nChandra attached these files: read each one first with the Read tool "
                             "(it opens PDFs and images), and pass the relevant paths on to your agents:\n"
                             + "\n".join(f"- {f}" for f in files))
                via = CEO if data.get("via") == CEO else None
                office.loop.call_soon_threadsafe(office.goals.put_nowait, (text, who, project, via))
                return self.send_json({"ok": True})
            if self.path == "/api/approve":
                office.loop.call_soon_threadsafe(office.on_approve, bool(data.get("ok")))
                return self.send_json({"ok": True})
            self.send_json({"error": "unknown"}, 404)

        def upload(self, size):
            from urllib.parse import parse_qs, urlparse
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
            if not 0 < size <= MAX_UPLOAD:
                return self.send_json({"error": "files must be under 25 MB"}, 413)
            path = upload_path(office.project, name)
            if path is None:
                return self.send_json({"error": "only PDF, image or text files"}, 415)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.rfile.read(size))
            office.log("chandra", "FILE", path.name, path=str(path))
            self.send_json({"path": str(path), "name": path.name})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"3D office not started ({e}); another office may be running.", flush=True)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\033[1;35m3D office → http://localhost:{PORT}\033[0m", flush=True)


def read_terminal(office):
    def pump():
        for line in sys.stdin:
            office.loop.call_soon_threadsafe(office.on_line, line)
        office.loop.call_soon_threadsafe(office.goals.put_nowait, ("exit", "chandra", None, None))  # Ctrl-D
    threading.Thread(target=pump, daemon=True).start()


def manager_prompt(project):
    staff = "\n".join(f"- {name} ({team}): {desc}" for name, (team, desc, *_r) in ROLES.items())
    return f"""You are the CTO of Chandra's AI office (software + hardware). Chandra is the founder; Buddy, Chandra's desktop
companion, is the CEO and passes on most goals and relays your reports and approval requests to Chandra.
You report to Buddy (and through him to Chandra). You lead engineering (software and hardware), product &
research, and operations (parts sourcing); your staff does the work.
Project folder for this job: {project}

Your staff (delegate with the Agent tool, using these exact names as subagent_type):
{staff}

How you run the office:
1. Spec before build. For a new product or feature (anything more than a quick fix or a question), nobody builds first:
   a. Kickoff: restate the goal, the users, the deadline and the budget in a few lines; ask Chandra only what you can't infer.
   b. Spec: product-manager writes a one-page spec with acceptance criteria (pull in researcher-writer if facts are missing).
      Have both team leads (and operations-manager for budget and lead times) review it for gaps and different readings.
   c. Approval: stop and show Chandra the spec summary and its file, and ask "Approve the spec?". End your turn there; do not
      brief builders until Chandra (or Buddy for Chandra) says yes. Apply any changes asked for and ask again.
   d. Plan: team leads split their team's work (owner, inputs, output file, done-when); you merge it into one plan.
   Small fixes and questions skip straight to step 2.
   Team leads: software-architect leads software, hardware-architect leads the hardware lab; they also sign off integration.
2. Give each agent a self-contained brief: the goal, the files to read, what to produce and where.
3. Quality gates: a team lead's SIGN-OFF for multi-part team work; code goes to code-reviewer (and qa-tester when testable) before you call it done;
   hardware designs get a hw-test-engineer plan; once there is a BOM, operations-manager makes the purchase plan, budget
   and build schedule; anything security-relevant goes to security-engineer.
4. If an agent reports work for another role, hand it on. Re-brief and retry when a gate fails (at most 2 rounds).
5. Do small coordination edits yourself; delegate real work.
6. Finish with a report for Chandra: what was done, the files produced, open questions, and suggested next steps.
Messages starting "(Chandra, about <role>)" are for that agent: brief them with it, or answer from their last report.
Keep updates short. Chandra is learning agentic AI, so name the pattern you're using when it helps (spec → approve → plan → delegate → review).
{HOUSE_RULES}"""


async def run(goal, project, once):
    office = Office(project, can_ask=sys.stdin.isatty() or not once)
    start_web(office)
    if sys.stdin.isatty():
        read_terminal(office)
    if goal:
        office.goals.put_nowait((goal, "chandra", None, None))
    agents = {name: AgentDefinition(description=desc, prompt=f"{how}\n{HOUSE_RULES}", tools=tools, model="inherit")
              for name, (_team, desc, tools, how) in ROLES.items()}

    def options(folder):
        return ClaudeAgentOptions(
            max_buffer_size=50 * 1024 * 1024,  # ponytail: big PDF reads overflow the 1 MB default
            model=MODEL, cwd=str(folder), agents=agents, system_prompt=manager_prompt(folder),
            add_dirs=[str(Path("~/Documents/chandra/AgenticAI-Brain").expanduser())],
            can_use_tool=office.can_use_tool, setting_sources=[],
            hooks={"PreToolUse": [HookMatcher(hooks=[office.pre_tool])],
                   "SubagentStart": [HookMatcher(hooks=[office.subagent_start])],
                   "SubagentStop": [HookMatcher(hooks=[office.subagent_stop])]},
        )

    async with contextlib.AsyncExitStack() as stack:
        client = None
        while True:
            if once and office.goals.empty():
                return
            if sys.stdin.isatty():
                print("\n\033[1;36mYou → CTO\033[0m (type a goal, or 'exit'):", flush=True)
            goal, who, folder, via = await office.goals.get()
            if goal in ("exit", "quit"):
                return
            if client is None or (folder and folder != office.project):
                # a new project: a fresh manager session working in that folder
                await stack.aclose()
                office.project = folder or office.project
                client = await stack.enter_async_context(ClaudeSDKClient(options=options(office.project)))
            office.log(who, "GOAL", goal, project=office.project.name, **({"via": via} if via else {}))
            office.set(MANAGER, status="working", task=goal[:80])
            ceo_job = CEO in (who, via)
            if ceo_job:
                office.set(CEO, status="working", task=goal[:80])
                goal = f"(From Buddy, the CEO, passing on Chandra's request) {goal}"
            await client.query(goal)
            # Sub-agents may run in the background; when one finishes, the manager gets a new turn
            # automatically. Keep reading turns until nobody is working.
            while True:
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                        text = "\n".join(b.text for b in msg.content if isinstance(b, TextBlock)).strip()
                        if text:
                            office.log(MANAGER, "SAY", text)
                    elif isinstance(msg, ResultMessage):
                        cost = f" · ${msg.total_cost_usd:.2f}" if msg.total_cost_usd else ""
                        office.log(MANAGER, "TURN", f"{msg.num_turns} turns · {msg.duration_ms // 1000}s{cost}")
                if not office.busy():
                    break
            office.set(MANAGER, status="idle", task="", done=office.board[MANAGER]["done"] + 1)
            if ceo_job:
                office.set(CEO, status="idle", task="", done=office.board[CEO]["done"] + 1)
            office.log(MANAGER, "DONE", "all agents finished")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("goal", nargs="?", help="first goal for the CTO")
    ap.add_argument("--project", default=str(HERE / "workspace"), help="folder the team works in")
    ap.add_argument("--once", action="store_true", help="exit after the given goal")
    a = ap.parse_args()
    project = Path(a.project).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run(a.goal, project, a.once))
    except KeyboardInterrupt:
        print("\noffice closed")


if __name__ == "__main__":
    main()

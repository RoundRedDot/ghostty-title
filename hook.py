#!/usr/bin/env python3
"""Claude Code hook → writes state file + spawns animation daemon.

Invoked once per hook event. Stateless: persists to JSON state file,
ensures the per-tty daemon is running, exits.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
DAEMON = ROOT / "daemon.py"


def find_tty() -> str | None:
    """Walk parent process tree to locate the controlling tty.

    Hooks run as children of Claude Code whose stdio is captured.
    We need the user-facing pty device for OSC writes.
    """
    if os.environ.get("TERM_PROGRAM") != "ghostty":
        return None
    pid = os.getpid()
    for _ in range(20):
        if pid <= 1:
            return None
        try:
            tty = subprocess.check_output(
                ["ps", "-o", "tty=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
        except subprocess.CalledProcessError:
            return None
        if tty and tty != "??":
            return f"/dev/{tty}"
        try:
            ppid = subprocess.check_output(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            pid = int(ppid)
        except (subprocess.CalledProcessError, ValueError):
            return None
    return None


def describe_tool(tool: str, tool_input: dict) -> str:
    """Short human-readable label for the operation in progress."""
    if tool == "Bash":
        cmd = (tool_input.get("command") or "").strip().splitlines()
        return cmd[0][:48] if cmd else ""
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        fp = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return os.path.basename(fp)
    if tool == "Read":
        return os.path.basename(tool_input.get("file_path", ""))
    if tool in ("Grep", "Glob"):
        return (tool_input.get("pattern") or "")[:32]
    if tool == "Task":
        return tool_input.get("subagent_type") or "subagent"
    if tool == "WebFetch":
        return (tool_input.get("url") or "")[:40]
    if tool == "WebSearch":
        return (tool_input.get("query") or "")[:32]
    if tool == "AskUserQuestion":
        return "asking"
    return ""


def ensure_daemon(state_file: Path, pid_file: Path) -> None:
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            os.kill(pid, 0)
            return  # alive
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    log = state_file.with_suffix(".log")
    proc = subprocess.Popen(
        [sys.executable, str(DAEMON), str(state_file)],
        stdout=open(log, "a"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    pid_file.write_text(str(proc.pid))


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else "Unknown"
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    tty = find_tty()
    if not tty:
        return 0

    key = tty.replace("/", "_").strip("_")
    state_file = STATE_DIR / f"{key}.json"
    pid_file = STATE_DIR / f"{key}.pid"

    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except json.JSONDecodeError:
            state = {}

    cwd = payload.get("cwd") or os.getcwd()
    state["tty"] = tty
    state["project"] = os.path.basename(cwd) or "claude"
    state["updated"] = time.time()

    if event == "SessionStart":
        state["state"] = "idle"
        state.pop("tool", None)
        state.pop("target", None)
    elif event == "UserPromptSubmit":
        # Genuine "no tool yet" thinking — clear context
        state["state"] = "thinking"
        state.pop("tool", None)
        state.pop("target", None)
    elif event == "PreToolUse":
        tool = payload.get("tool_name", "")
        state["state"] = "tool"
        state["tool"] = tool
        state["target"] = describe_tool(tool, payload.get("tool_input") or {})
    elif event == "PostToolUse":
        # Between tools — Claude is reasoning. Keep tool/target so the title
        # still shows the most recent operation; just switch animation to
        # the "thinking" spinner so the user can tell tool vs reasoning apart.
        state["state"] = "thinking"
    elif event == "Stop":
        state["state"] = "idle"
        state.pop("tool", None)
        state.pop("target", None)
    elif event == "Notification":
        state["state"] = "asking"
    elif event == "PreCompact":
        state["state"] = "compacting"
    elif event == "SessionEnd":
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 15)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
            pid_file.unlink(missing_ok=True)
        # Restore a plain title before exiting
        try:
            with open(tty, "w") as fh:
                fh.write(f"\033]2;{state['project']}\a")
        except OSError:
            pass
        state_file.unlink(missing_ok=True)
        return 0

    state_file.write_text(json.dumps(state))
    ensure_daemon(state_file, pid_file)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Hooks must never break Claude Code — swallow and log.
        try:
            (STATE_DIR / "hook-error.log").write_text(
                f"{time.time()} {sys.argv} {sys.exc_info()}\n"
            )
        except Exception:
            pass
        sys.exit(0)

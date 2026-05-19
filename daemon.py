#!/usr/bin/env python3
"""Per-tty animation loop. One instance per terminal.

Reads JSON state + config, renders animated frame, writes OSC 2 to the tty.
Config is reloaded every frame so edits to config.json take effect immediately.
"""
from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path

STATE_FILE = Path(sys.argv[1])
ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"


DEFAULT_CONFIG = {
    "frame_interval": 0.12,
    "idle_frame_interval": 0.6,
    "stale_after_seconds": 300,
    "state_intervals": {"asking": 0.4},
    "title_format": "[{project}] {anim} {body}",
    "tool_body_format_with_target": "{icon} {target}",
    "tool_body_format_no_target":   "{icon} {tool}",
    "max_target_length": 48,
    "animations": {
        "tool":       "bars",
        "thinking":   "spinner",
        "asking":     "blink_question",
        "compacting": "wave",
        "idle":       "pulse",
    },
    "labels": {
        "thinking": "thinking", "asking": "waiting for you", "compacting": "compacting",
    },
    "frame_banks": {
        "spinner": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
        "wave":    ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂"],
        "pulse":   ["◐", "◓", "◑", "◒"],
        "bars":    ["▏", "▎", "▍", "▌", "▋", "▊", "▉", "█", "▉", "▊", "▋", "▌", "▍", "▎"],
        "blink_question": ["❓", "❔"],
        "static":  [""],
    },
    "tool_icons": {
        "Bash": "$", "Edit": "✎", "Write": "✎", "MultiEdit": "✎", "NotebookEdit": "✎",
        "Read": "…", "Grep": "⌕", "Glob": "⌕", "Task": "❖",
        "WebFetch": "↯", "WebSearch": "⌕", "AskUserQuestion": "?",
    },
    "default_tool_icon": "✻",
}


def load_config() -> dict:
    """Read config.json, fall back to defaults on any error."""
    try:
        user = json.loads(CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_CONFIG
    # shallow merge — user values override defaults at the top level
    cfg = {**DEFAULT_CONFIG, **{k: v for k, v in user.items() if not k.startswith("_")}}
    # nested merges so partial overrides don't wipe defaults
    for k in ("animations", "labels", "frame_banks", "tool_icons", "state_intervals"):
        cfg[k] = {**DEFAULT_CONFIG.get(k, {}), **user.get(k, {})}
    return cfg


def pick_frame(cfg: dict, state_name: str, i: int) -> str:
    bank_name = cfg["animations"].get(state_name, "static")
    bank = cfg["frame_banks"].get(bank_name) or cfg["frame_banks"]["static"]
    return bank[i % len(bank)] if bank else ""


def render(cfg: dict, state: dict, i: int) -> str:
    project = state.get("project") or "claude"
    st = state.get("state", "idle")
    fmt = cfg["title_format"]

    tool = state.get("tool", "")
    target = (state.get("target") or "")[: cfg["max_target_length"]]
    has_op = bool(tool or target)

    if st == "tool":
        icon = cfg["tool_icons"].get(tool, cfg["default_tool_icon"])
        body_fmt = cfg["tool_body_format_with_target"] if target else cfg["tool_body_format_no_target"]
        body = body_fmt.format(icon=icon, tool=tool, target=target)
        anim = pick_frame(cfg, "tool", i)
        return fmt.format(project=project, anim=anim, body=body)

    if st == "thinking":
        anim = pick_frame(cfg, "thinking", i)
        if has_op:
            # Carry forward the last operation so the user sees what just happened
            icon = cfg["tool_icons"].get(tool, cfg["default_tool_icon"])
            body_fmt = cfg["tool_body_format_with_target"] if target else cfg["tool_body_format_no_target"]
            body = body_fmt.format(icon=icon, tool=tool, target=target)
        else:
            body = cfg["labels"].get("thinking", "thinking")
        return fmt.format(project=project, anim=anim, body=body)

    if st in ("compacting", "asking"):
        anim = pick_frame(cfg, st, i)
        label = cfg["labels"].get(st, st)
        return fmt.format(project=project, anim=anim, body=label)

    # idle
    anim = pick_frame(cfg, "idle", i // 4)  # slow down idle animation
    return fmt.format(project=project, anim=anim, body="").rstrip()


def main() -> None:
    tty_path: str | None = None
    tty_fh = None
    last_state: dict = {}
    i = 0

    def cleanup(*_):
        if tty_fh:
            try:
                tty_fh.write(f"\033]2;{last_state.get('project','claude')}\a")
                tty_fh.flush()
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    while True:
        cfg = load_config()

        try:
            mtime = STATE_FILE.stat().st_mtime
        except FileNotFoundError:
            break
        if time.time() - mtime > cfg["stale_after_seconds"]:
            break

        try:
            state = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            state = last_state
        last_state = state

        new_tty = state.get("tty")
        if not new_tty:
            break
        if new_tty != tty_path:
            if tty_fh:
                try: tty_fh.close()
                except Exception: pass
            try:
                tty_fh = open(new_tty, "w")
                tty_path = new_tty
            except OSError:
                break

        if tty_fh is None:
            break
        try:
            tty_fh.write(f"\033]2;{render(cfg, state, i)}\a")
            tty_fh.flush()
        except OSError:
            break

        state_name = state.get("state", "idle")
        override = cfg.get("state_intervals", {}).get(state_name)
        if override is not None:
            interval = override
        elif state_name in ("tool", "thinking", "compacting"):
            interval = cfg["frame_interval"]
        else:
            interval = cfg["idle_frame_interval"]
        time.sleep(interval)
        i += 1

    cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            (STATE_FILE.parent / "daemon-error.log").write_text(
                f"{time.time()} {sys.exc_info()}\n"
            )
        except Exception:
            pass

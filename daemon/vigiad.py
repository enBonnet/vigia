#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vigiad — Vigía agent watcher daemon.

Watches AI coding agents on this machine and exposes their state over the
session D-Bus for the Vigía GNOME Shell extension:

  name:   org.vigia.Watcher      object: /org/vigia/Watcher

  methods:
    Ping()                          -> s
    List()                          -> s   (JSON snapshot of all agents)
    Report(agent, session, state, title, detail) -> b   (hook ingest)

  signals:
    AgentChanged(s id, s json)
    AgentGone(s id)

Sources:
  * OpenCode   — polls `opencode api session.list` + `permission.request.list`.
                 A session is busy while time.updated > time.idle; pending
                 entries in permission.request.list mark it as "question".
  * Claude Code — receives state pushed by the vigia-claude.sh hook reporter.
  * Generic     — /proc scan of configured process names (busy while alive).

States: idle | busy | question | done
"""

import json
import os
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.vigia.Watcher"
OBJ_PATH = "/org/vigia/Watcher"
IFACE = "org.vigia.Watcher"
VERSION = "1.0"

NODE_XML = f"""<node>
  <interface name="{IFACE}">
    <method name="Ping">
      <arg name="pong" direction="out" type="s"/>
    </method>
    <method name="List">
      <arg name="state" direction="out" type="s"/>
    </method>
    <method name="Report">
      <arg name="agent" direction="in" type="s"/>
      <arg name="session" direction="in" type="s"/>
      <arg name="state" direction="in" type="s"/>
      <arg name="title" direction="in" type="s"/>
      <arg name="detail" direction="in" type="s"/>
      <arg name="ok" direction="out" type="b"/>
    </method>
    <signal name="AgentChanged">
      <arg name="id" type="s"/>
      <arg name="json" type="s"/>
    </signal>
    <signal name="AgentGone">
      <arg name="id" type="s"/>
    </signal>
    <property name="Version" type="s" access="read"/>
  </interface>
</node>"""

# policy knobs
IDLE_DISPLAY_WINDOW = 1800     # keep idle sessions listed for 30 min
OC_BUSY_STALE_SECS = 6 * 3600  # opencode busy believed stale only after 6 h
BUSY_STALE_SECS = 1800         # claude "busy" with no refresh -> idle
DONE_HOLD_SECS = 120           # "done" stays flagged before decaying to idle
QUESTION_MAX_SECS = 12 * 3600  # questions expire after 12 h
IDLE_TTL = 24 * 3600           # idle claude entries drop after a day

VALID_STATES = ("idle", "busy", "question", "done")


class Settings:
    """Reads the extension's gsettings schema; falls back to defaults."""

    DEFAULTS = {
        "source-opencode": True,
        "source-claude": True,
        "source-generic": True,
        "generic-processes": "codex,aider,goose,gemini,cursor-agent",
        "poll-interval": 4,
    }

    def __init__(self):
        try:
            src = Gio.SettingsSchemaSource.get_default()
            schema = src.lookup("org.gnome.shell.extensions.vigia", False) if src else None
            self._s = Gio.Settings.new("org.gnome.shell.extensions.vigia") if schema else None
        except Exception:
            self._s = None

    def get(self, key):
        try:
            if self._s is not None:
                return self._s.get_value(key).unpack()
        except Exception:
            pass
        return self.DEFAULTS.get(key)


def find_opencode():
    for cand in (
        "opencode",  # PATH
        os.path.expanduser("~/.opencode/bin/opencode"),
        os.path.expanduser("~/.local/bin/opencode"),
    ):
        found = None
        if os.path.sep not in cand:
            for d in os.environ.get("PATH", "").split(os.pathsep):
                p = os.path.join(d, cand)
                if os.access(p, os.X_OK):
                    found = p
                    break
        elif os.access(cand, os.X_OK):
            found = cand
        if found:
            return found
    return None


class Vigia:
    def __init__(self, debug=False):
        self._agents = {}  # id -> dict(agent, state, title, project, model, detail, since, updated)
        self._conn = None
        self._settings = Settings()
        self._debug = debug
        self._oc_bin = find_opencode()
        self._oc_fail_logged = 0

    # ------------------------------------------------------------------ util

    def log(self, msg):
        sys.stdout.write(f"vigiad: {msg}\n")
        sys.stdout.flush()

    def dbg(self, msg):
        if self._debug:
            self.log(f"debug: {msg}")

    def enabled(self, source):
        return bool(self._settings.get(source))

    # ----------------------------------------------------------- agent state

    def upsert(self, agent_id, agent, state, title, detail="", project="", model=""):
        if state not in VALID_STATES:
            state = "idle"
        now = time.time()
        old = self._agents.get(agent_id)
        since = old["since"] if old and old["state"] == state else now
        entry = {
            "agent": agent,
            "state": state,
            "title": title or "",
            "project": project or "",
            "model": model or "",
            "detail": detail or "",
            "since": since,
            "updated": now,
        }
        if (
            old
            and old["state"] == entry["state"]
            and old["title"] == entry["title"]
            and old["project"] == entry["project"]
            and old["model"] == entry["model"]
            and old["detail"] == entry["detail"]
            and now - old["updated"] < 15
        ):
            old["updated"] = now  # nothing visible changed; skip the signal
            return
        self._agents[agent_id] = entry
        self.dbg(f"{agent_id} -> {state}")
        self._emit_changed(agent_id, entry)

    def _transition(self, agent_id, state):
        e = self._agents.get(agent_id)
        if not e:
            return
        e["state"] = state
        e["since"] = e["updated"] = time.time()
        self.dbg(f"{agent_id} ~ {state}")
        self._emit_changed(agent_id, e)

    def remove(self, agent_id):
        if agent_id in self._agents:
            del self._agents[agent_id]
            self.dbg(f"{agent_id} gone")
            self._emit_gone(agent_id)

    def _emit_changed(self, agent_id, entry):
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(
                None, OBJ_PATH, IFACE, "AgentChanged",
                GLib.Variant("(ss)", (agent_id, json.dumps(entry))),
            )
        except Exception as exc:  # connection died; next owner change fixes it
            self.dbg(f"emit failed: {exc}")

    def _emit_gone(self, agent_id):
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(
                None, OBJ_PATH, IFACE, "AgentGone", GLib.Variant("(s)", (agent_id,))
            )
        except Exception as exc:
            self.dbg(f"emit failed: {exc}")

    # ------------------------------------------------------- hook ingest API

    def report(self, agent, session, state, title, detail):
        """Entry point for hook reporters (Claude Code etc.)."""
        if state == "gone":
            self.remove(f"claude:{session or 'default'}")
            return True
        self.upsert(
            f"claude:{session or 'default'}",
            "claude",
            state,
            title or "Claude Code",
            detail or "",
        )
        return True

    # ------------------------------------------------------------ OpenCode

    def _oc_api(self, op, timeout=15):
        if not self._oc_bin:
            return None
        try:
            out = subprocess.run(
                [self._oc_bin, "api", op],
                capture_output=True, text=True, timeout=timeout,
            )
            if out.returncode != 0:
                raise RuntimeError(out.stderr.strip()[:200] or f"rc={out.returncode}")
            return json.loads(out.stdout)
        except FileNotFoundError:
            self._oc_missing()
        except subprocess.TimeoutExpired:
            self._oc_fail(f"{op} timed out")
        except json.JSONDecodeError:
            self._oc_fail(f"{op} returned non-JSON")
        except Exception as exc:
            self._oc_fail(f"{op}: {exc}")
        return None

    def _oc_missing(self):
        if time.time() - self._oc_fail_logged > 300:
            self._oc_fail_logged = time.time()
            self.log("opencode binary not found; OpenCode source disabled")

    def _oc_fail(self, msg):
        if time.time() - self._oc_fail_logged > 300:
            self._oc_fail_logged = time.time()
            self.log(f"opencode api issue (will retry quietly): {msg}")

    def poll_opencode(self):
        if not self.enabled("source-opencode"):
            for aid in [a for a in self._agents if a.startswith("opencode:")]:
                self.remove(aid)
            return
        sessions = self._oc_api("session.list") or {}
        perms = self._oc_api("permission.request.list") or {}
        now = time.time()
        seen = set()

        ask = set()
        for p in perms.get("data") or []:
            if not isinstance(p, dict):
                continue
            sid = p.get("sessionID") or p.get("session_id")
            if not sid and isinstance(p.get("session"), dict):
                sid = p["session"].get("id")
            if sid:
                ask.add(str(sid))
            else:  # session unknown: flag the whole project as asking
                pid = p.get("projectID") or p.get("project_id") or ""
                if pid:
                    ask.add(f"project:{pid}")

        for s in sessions.get("data") or []:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "")
            if not sid:
                continue
            t = s.get("time") or {}
            updated = (t.get("updated") or 0) / 1000.0
            idle = t.get("idle")
            idle = idle / 1000.0 if idle else 0.0
            busy = updated > idle and updated > now - OC_BUSY_STALE_SECS

            aid = f"opencode:{sid}"
            if not busy and updated < now - IDLE_DISPLAY_WINDOW:
                self.remove(aid)
                continue

            state = "busy" if busy else "idle"
            if sid in ask or f"project:{s.get('projectID') or ''}" in ask:
                state = "question"
            model = s.get("model")
            model = model.get("id") if isinstance(model, dict) else None
            self.upsert(
                aid, "opencode", state,
                s.get("title") or "OpenCode session",
                project=(s.get("location") or {}).get("directory") or "",
                model=model,
            )
            seen.add(aid)

        # prune anything we no longer see at all
        for aid in [a for a in self._agents if a.startswith("opencode:") and a not in seen]:
            if now - self._agents[aid]["updated"] > IDLE_DISPLAY_WINDOW:
                self.remove(aid)

    # -------------------------------------------------------- generic procs

    def poll_generic(self):
        if not self.enabled("source-generic"):
            for aid in [a for a in self._agents if a.startswith("proc:")]:
                self.remove(aid)
            return
        raw = str(self._settings.get("generic-processes") or "")
        names = {n.strip() for n in raw.split(",") if n.strip()}
        if not names:
            return
        counts = {}
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm") as f:
                    comm = f.read().strip()
            except OSError:
                continue
            if comm in names:
                counts[comm] = counts.get(comm, 0) + 1
        for name in names:
            aid = f"proc:{name}"
            n = counts.get(name, 0)
            if n:
                self.upsert(aid, name, "busy", name, f"{n} process{'es' if n > 1 else ''} running")
            else:
                self.remove(aid)
        # drop entries whose process name is no longer configured
        for aid in [a for a in self._agents if a.startswith("proc:")]:
            if aid[5:] not in names:
                self.remove(aid)

    # -------------------------------------------------------------- aging

    def age_out(self):
        now = time.time()
        for aid, e in list(self._agents.items()):
            if e["agent"] != "claude":
                continue
            st = e["state"]
            if st == "done" and now - e["since"] > DONE_HOLD_SECS:
                self._transition(aid, "idle")
            elif st == "busy" and now - e["updated"] > BUSY_STALE_SECS:
                self._transition(aid, "idle")
            elif st == "question" and now - e["since"] > QUESTION_MAX_SECS:
                self._transition(aid, "idle")
            elif st == "idle" and now - e["updated"] > IDLE_TTL:
                self.remove(aid)

    # ---------------------------------------------------------------- D-Bus

    def on_bus_acquired(self, conn, name):
        self._conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(NODE_XML)
        conn.register_object(OBJ_PATH, node.interfaces[0], self._on_method_call, None, None)
        self.log(f"ready on {BUS_NAME} ({len(self._agents)} agents tracked)")

    def on_name_lost(self, conn, name):
        self.log(f"could not own {BUS_NAME}; is another vigiad running?")
        sys.exit(1)

    def _on_method_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            if method == "Ping":
                invocation.return_value(GLib.Variant("(s)", (f"vigia {VERSION}",)))
            elif method == "List":
                payload = json.dumps({"version": VERSION, "agents": self._agents})
                invocation.return_value(GLib.Variant("(s)", (payload,)))
            elif method == "Report":
                agent, session, state, title, detail = params.unpack()
                ok = self.report(agent, session, state, title, detail)
                invocation.return_value(GLib.Variant("(b)", (ok,)))
            else:
                invocation.return_error(
                    Gio.DBusError.new_for_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", "no such method")
                )
        except Exception as exc:
            self.dbg(f"method {method} failed: {exc}")

    # ---------------------------------------------------------------- main

    def collect_once(self):
        self.poll_opencode()
        self.poll_generic()
        return self._agents


def main():
    debug = "--debug" in sys.argv
    once = "--once" in sys.argv
    v = Vigia(debug=debug)

    if once:
        print(json.dumps({"agents": v.collect_once()}, indent=2))
        return

    loop = GLib.MainLoop()
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix  # noqa: F401
        GLibUnix.signal_add(GLibUnix.SignalLevel.INT, loop.quit)
        GLibUnix.signal_add(GLibUnix.SignalLevel.TERM, loop.quit)
    except Exception:
        import signal as _signal
        _signal.signal(_signal.SIGINT, lambda *_: loop.quit())
        _signal.signal(_signal.SIGTERM, lambda *_: loop.quit())

    def tick_polls():
        v.poll_opencode()
        GLib.timeout_add(max(2, int(v._settings.get("poll-interval"))) * 1000, tick_polls)
        return GLib.SOURCE_REMOVE

    def tick_generic():
        v.poll_generic()
        GLib.timeout_add(5000, tick_generic)
        return GLib.SOURCE_REMOVE

    def tick_age():
        v.age_out()
        GLib.timeout_add(5000, tick_age)
        return GLib.SOURCE_REMOVE

    Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.REPLACE,
        v.on_bus_acquired,
        None,
        v.on_name_lost,
    )
    tick_polls()
    tick_generic()
    tick_age()
    v.log(f"vigiad {VERSION} started (debug={debug})")
    loop.run()


if __name__ == "__main__":
    main()

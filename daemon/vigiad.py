#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vigiad — VigIA agent watcher daemon.

Watches AI coding agents on this machine and exposes their state over the
session D-Bus for the VigIA GNOME Shell extension:

  name:   org.vigia.Watcher      object: /org/vigia/Watcher

  methods:
    Ping()                          -> s
    List()                          -> s   (JSON snapshot of all agents)
    Report(agent, session, state, title, detail, pids) -> b  (hook ingest)

  signals:
    AgentChanged(s id, s json)
    AgentGone(s id)

Sources:
  * OpenCode   — polls `opencode api session.list` + `permission.request.list`
                 + `session.active`. Busy comes straight from the server's own
                 active-run list; when a run ends, working sessions flash
                 "done" and then settle into the idle history. Pending
                 permission entries mark "question". If the API is
                 unreachable, state is kept (or demoted to idle on a total
                 outage).
  * Claude Code — receives state pushed by the vigia-claude.sh hook reporter.
                 Entries whose reported PIDs are all gone settle to idle
                 within seconds (any state); "done" flashes briefly, then the
                 entry settles to idle too.
  * Generic     — /proc scan of configured process names (busy while alive).

History: every source keeps finished agents listed as idle rows for a
configurable window (gsettings `history-hours`, default 72 h since their
last activity); anything idle past the window leaves the list. 0 drops
entries as soon as they stop working.

States: idle | busy | question | done
"""

import json
import os
import signal
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.vigia.Watcher"
OBJ_PATH = "/org/vigia/Watcher"
IFACE = "org.vigia.Watcher"
VERSION = "1.1"

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
      <arg name="pids" direction="in" type="s"/>
      <arg name="ok" direction="out" type="b"/>
    </method>
    <method name="StopAll">
      <arg name="reason" direction="in" type="s"/>
      <arg name="summary" direction="out" type="s"/>
    </method>
    <signal name="AgentChanged">
      <arg name="id" type="s"/>
      <arg name="json" type="s"/>
    </signal>
    <signal name="AgentGone">
      <arg name="id" type="s"/>
    </signal>
  </interface>
</node>"""

# policy knobs
BUSY_STALE_SECS = 1800         # claude "busy" with no refresh -> idle
DONE_HOLD_SECS = 120           # "done" flash before settling to idle history
QUESTION_MAX_SECS = 12 * 3600  # questions expire after 12 h

VALID_STATES = ("idle", "busy", "question", "done")


class Settings:
    """Reads the extension's gsettings schema; falls back to defaults."""

    DEFAULTS = {
        "source-opencode": True,
        "source-claude": True,
        "source-generic": True,
        "generic-processes": "codex,aider,goose,gemini,cursor-agent",
        "poll-interval": 4,
        "history-hours": 72,
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

    def history_secs(self):
        """Retention window for idle entries, in seconds (read live).

        Agents stay listed as idle rows this long after their last activity;
        0 drops them as soon as they stop working.
        """
        return max(0, int(self._settings.get("history-hours") or 0)) * 3600

    # ----------------------------------------------------------- agent state

    def upsert(self, agent_id, agent, state, title, detail="", project="", model="", pids=""):
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
            "pids": pids or "",
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

    def report(self, agent, session, state, title, detail, pids=""):
        """Entry point for hook reporters (Claude Code etc.)."""
        if state == "gone":
            aid = f"claude:{session or 'default'}"
            if aid in self._agents and self.history_secs() > 0:
                # session ended cleanly: settle into the idle history instead
                # of dropping — but forget the PIDs first, the CLI is exiting
                # and the dead-PID check would remove the fresh history row
                self._agents[aid]["pids"] = ""
                self._transition(aid, "idle")
            else:
                self.remove(aid)
            return True
        self.upsert(
            f"claude:{session or 'default'}",
            "claude",
            state,
            title or "Claude Code",
            detail or "",
            pids=pids or "",
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
        sessions = self._oc_api("session.list")
        perms = self._oc_api("permission.request.list")
        active = self._oc_api("session.active")
        if sessions is None or active is None:
            if sessions is None and active is None:
                # service down entirely: nothing can be running
                for aid in [a for a in self._agents
                            if a.startswith("opencode:")
                            and self._agents[a]["state"] in ("busy", "question")]:
                    self._transition(aid, "idle")
                return
            self.dbg("opencode api partial failure; keeping previous state")
            return
        sessions, perms = sessions or {}, perms or {}
        now = time.time()
        window = self.history_secs()
        seen = set()

        # sessions with a run in flight right now — the server's own,
        # authoritative busy signal (SessionActive.type is always "running")
        running = set()
        for sid, info in (active.get("data") or {}).items():
            if isinstance(info, dict) and info.get("type") == "running":
                running.add(str(sid))

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
            aid = f"opencode:{sid}"

            busy = sid in running
            question = sid in ask or f"project:{s.get('projectID') or ''}" in ask
            state = "question" if question else ("busy" if busy else "idle")

            t = s.get("time") or {}
            updated = (t.get("updated") or 0) / 1000.0
            idle_ts = (t.get("idle") or 0) / 1000.0

            # subagent sessions (@explore/@review/…) only matter while
            # active; idle sessions whose last activity fell outside the
            # history window never list at all — and are dropped even when
            # we already track them (this must run before the tracked-idle
            # short-circuit below)
            if state == "idle" and (
                    bool(s.get("parentID"))
                    or max(updated, idle_ts) < now - window):
                self.remove(aid)
                continue

            old = self._agents.get(aid)
            if state == "idle" and old:
                if old["state"] in ("busy", "question"):
                    # the run just ended (or the process died) — green "done"
                    # flash; age_out settles it to idle history afterwards
                    self._transition(aid, "done")
                seen.add(aid)  # kept as idle history until the window is up
                continue

            model = s.get("model")
            model = model.get("id") if isinstance(model, dict) else None
            self.upsert(
                aid, "opencode", state,
                s.get("title") or "OpenCode session",
                project=(s.get("location") or {}).get("directory") or "",
                model=model,
            )
            seen.add(aid)

        # prune anything we no longer see at all — such a session cannot
        # still be working, so demote it first and let the history window
        # decide when the idle row goes
        for aid in [a for a in self._agents if a.startswith("opencode:") and a not in seen]:
            if self._agents[aid]["state"] in ("busy", "question"):
                self._transition(aid, "idle")
            elif now - self._agents[aid]["updated"] > window:
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
        pids_by_name = {}
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm") as f:
                    comm = f.read().strip()
            except OSError:
                continue
            if comm in names:
                pids_by_name.setdefault(comm, []).append(int(pid))
        window = self.history_secs()
        for name in names:
            aid = f"proc:{name}"
            found = pids_by_name.get(name, [])
            if found:
                n = len(found)
                self.upsert(aid, name, "busy", name,
                            f"{n} process{'es' if n > 1 else ''} running",
                            pids=",".join(str(p) for p in found))
            elif aid in self._agents:
                if window > 0:
                    # process gone: settle into the idle history
                    self._transition(aid, "idle")
                else:
                    self.remove(aid)
        # drop entries whose process name is no longer configured
        for aid in [a for a in self._agents if a.startswith("proc:")]:
            if aid[5:] not in names:
                self.remove(aid)

    def _oc_interrupt(self, session_id):
        """Gracefully stop the current OpenCode turn (POST /session/:id/interrupt)."""
        if not self._oc_bin:
            return False
        try:
            out = subprocess.run(
                [self._oc_bin, "api", "session.interrupt",
                 "--param", f"sessionID={session_id}"],
                capture_output=True, text=True, timeout=15)
            return out.returncode == 0
        except Exception as exc:
            self.dbg(f"interrupt {session_id} failed: {exc}")
            return False

    def stop_all(self, reason):
        """Emergency stop: interrupt/terminate every working agent."""
        summary = {"reason": reason, "stopped": {"opencode": [], "claude": [], "generic": []}, "failed": []}
        stopped, failed = summary["stopped"], summary["failed"]
        for aid, e in list(self._agents.items()):
            if e["state"] not in ("busy", "question"):
                continue
            if e["agent"] == "opencode":
                sid = aid.split(":", 1)[1]
                ok = self._oc_interrupt(sid)
                (stopped["opencode"] if ok else failed).append({"id": sid, "title": e["title"]})
                continue
            pids = [int(p) for p in (e.get("pids") or "").split(",") if p.strip().isdigit()]
            killed = []
            for pid in pids:
                try:
                    if e["agent"] == "claude":
                        # graceful interrupt of the CLI process (its Stop hook then reports)
                        with open(f"/proc/{pid}/comm") as f:
                            if f.read().strip() != "claude":
                                continue
                        os.kill(pid, signal.SIGINT)
                    else:
                        os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
                except (OSError, ValueError):
                    continue
            bucket = "claude" if e["agent"] == "claude" else "generic"
            (stopped[bucket] if killed else failed).append(
                {"id": aid, "title": e["title"], "pids": killed})
        self.log(f"stop_all({reason}): {json.dumps(summary)}")
        return summary

    # -------------------------------------------------------------- aging

    def age_out(self):
        window = self.history_secs()
        now = time.time()
        for aid, e in list(self._agents.items()):
            st = e["state"]

            # every source: idle entries leave the list once the history
            # window since their last activity is up (0 = right away)
            if st == "idle" and now - e["updated"] > window:
                self.remove(aid)
                continue

            # the green "done" flash settles into the idle history
            if st == "done" and now - e["since"] > DONE_HOLD_SECS:
                if window > 0:
                    self._transition(aid, "idle")
                else:
                    self.remove(aid)
                continue

            if e["agent"] == "opencode":
                continue
            if e["agent"] != "claude":
                continue

            # hooks tell us which processes belong to a session: if they are
            # all gone the session is over, whatever the last hook said —
            # settle it into the idle history rather than dropping it
            pids = [p for p in (e.get("pids") or "").split(",")
                    if p.strip().isdigit()]
            if pids and all(not os.path.exists(f"/proc/{p.strip()}")
                            for p in pids):
                self.dbg(f"{aid} pids gone -> "
                         f"{'idle' if window > 0 else 'remove'}")
                e["pids"] = ""   # they are dead; don't re-trigger this check
                if window > 0:
                    if st != "idle":
                        self._transition(aid, "idle")
                else:
                    self.remove(aid)
                continue

            if st == "busy" and now - e["updated"] > BUSY_STALE_SECS:
                self._transition(aid, "idle")
            elif st == "question" and now - e["since"] > QUESTION_MAX_SECS:
                self._transition(aid, "idle")

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
                agent, session, state, title, detail, pids = params.unpack()
                ok = self.report(agent, session, state, title, detail, pids)
                invocation.return_value(GLib.Variant("(b)", (ok,)))
            elif method == "StopAll":
                (reason,) = params.unpack()
                summary = self.stop_all(reason)
                invocation.return_value(GLib.Variant("(s)", (json.dumps(summary),)))
            else:
                invocation.return_error(
                    Gio.DBusError.new_for_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", "no such method")
                )
        except Exception as exc:
            self.dbg(f"method {method} failed: {exc}")
            # Always reply, even on failure: an unanswered invocation leaves the
            # caller waiting out its full timeout.
            try:
                invocation.return_error(Gio.DBusError.new_for_dbus_error(
                    "org.freedesktop.DBus.Error.Failed", str(exc)))
            except Exception:
                pass

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

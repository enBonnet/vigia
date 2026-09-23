#!/usr/bin/env python3
"""vigia-status — print the live agent state tracked by vigiad."""

import json
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402


def main():
    watch = "--watch" in sys.argv
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def fetch():
        res = conn.call_sync(
            "org.vigia.Watcher", "/org/vigia/Watcher", "org.vigia.Watcher",
            "List", None, None, Gio.DBusCallFlags.NONE, 2000, None)
        payload = res.unpack()[0]
        return json.loads(payload)

    def render(data):
        agents = data.get("agents", {})
        print(f"vigia daemon v{data.get('version')} — {len(agents)} agent(s)")
        for aid, e in sorted(agents.items(), key=lambda kv: kv[1]["updated"], reverse=True):
            print(f"  [{e['state']:8}] {aid[:44]:44} {e['title'][:38]}")
        if not agents:
            print("  (none)")

    render(fetch())
    if watch:
        loop = __import__("gi.repository.GLib", fromlist=["GLib"]).MainLoop()

        def on_signal(conn_, sender, path, iface, signal, params):
            if signal == "AgentChanged":
                aid, payload = params.unpack()
                print(f"changed: {aid} -> {json.loads(payload)['state']}")
                render(fetch())
            elif signal == "AgentGone":
                print(f"gone: {params.unpack()[0]}")
                render(fetch())

        conn.signal_subscribe(
            "org.vigia.Watcher", "org.vigia.Watcher", None, "/org/vigia/Watcher",
            None, Gio.DBusSignalFlags.NONE, on_signal)
        loop.run()


if __name__ == "__main__":
    main()

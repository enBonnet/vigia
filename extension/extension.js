// VigIA — GNOME Shell extension
// Top-bar lookout for AI coding agents: per-agent icons showing who is
// working, who needs you, who just finished — plus optional keep-awake.

import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Clutter from 'gi://Clutter';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const BUS_NAME = 'org.vigia.Watcher';
const OBJ_PATH = '/org/vigia/Watcher';
const IFACE = 'org.vigia.Watcher';

// Keep-awake implementation (GNOME 50 dropped org.gnome.SessionManager, so
// the classic Caffeine-style inhibit is gone). VigIA instead:
//   1. holds a logind "idle:sleep:handle-lid-switch" block inhibitor —
//      enforced by systemd-logind itself; lid state is ignored, and
//      auto-suspend is blocked on AC and battery alike;
//   2. holds the screen blank by setting org.gnome.desktop.session
//      idle-delay to 0 for the duration (original value restored on release).
// The ONLY surrender condition: battery critically low — VigIA watches
// UPower and releases everything so GNOME's critical-battery action can
// save the machine. Plugging in re-arms the hold automatically.

const STATE_ORDER = {busy: 0, question: 1, done: 2, idle: 3};
const STATE_COLOR = {busy: null, question: '#f6d32d', done: '#33d17a', idle: null};
const STATE_LABEL = {busy: 'working', question: 'needs you', done: 'done', idle: 'idle'};
// Brand glyphs (simple-icons, CC0) tinted by the state colors; the robot is
// the fallback for any agent without a mark. Keyed by the `agent` field the
// daemon reports (proc-scanned agents report their process name).
const AGENT_ICON = {
    opencode: 'vigia-oc-symbolic',
    claude: 'vigia-cc-symbolic',
    codex: 'vigia-cx-symbolic',            // ChatGPT knot
    gemini: 'vigia-gemini-symbolic',
    'cursor-agent': 'vigia-cursor-symbolic',
    cursor: 'vigia-cursor-symbolic',
    copilot: 'vigia-copilot-symbolic',
    windsurf: 'vigia-windsurf-symbolic',
    cline: 'vigia-cline-symbolic',
};
const GENERIC_ICON = 'vigia-generic-symbolic';
const BRAND_ICON = 'vigia-symbolic';
// Status glyphs shown in place of the old text labels: vendored shovel
// (working) and zzz (idle), plus themed icons for question/done.
const STATE_ICON_FILE = {busy: 'vigia-state-busy-symbolic', idle: 'vigia-state-idle-symbolic'};
const STATE_ICON_THEME = {question: 'dialog-question-symbolic', done: 'emblem-ok-symbolic'};
const MAX_PANEL_ICONS = 4;

function formatAge(seconds) {
    if (seconds < 60)
        return `${Math.max(1, Math.round(seconds))}s`;
    if (seconds < 3600)
        return `${Math.round(seconds / 60)}m`;
    return `${Math.round(seconds / 3600)}h`;
}

const VigiaIndicator = GObject.registerClass(
class VigiaIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'VigIA', false);
        this._extension = extension;
        this._settings = extension.getSettings();
        this._agents = {};
        this._daemonOk = false;
        this._busyIcons = [];
        this._pulseOn = false;
        this._fadedDone = new Set();
        this._fadeTimeouts = new Map();
        this._logindFd = null;
        this._savedIdleDelay = null;
        this._inhibited = false;
        this._proxy = null;
        this._sigId = null;
        this._ownerId = null;
        this._wasCritical = false;

        this._recoverBlankHold();

        this._panelBox = new St.BoxLayout({style_class: 'vigia-panel-box'});
        this.add_child(this._panelBox);

        this._buildMenu();
        this._render();

        this._keepAwakeId = this._settings.connect(
            'changed::keep-awake', () => this._updateInhibit());
        this._showIdleId = this._settings.connect(
            'changed::show-idle', () => this._render());

        this._ownerId = Gio.bus_watch_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            () => this._connectProxy(),
            () => this._onVanished());

        this._setupPowerMonitor();
    }

    // ------------------------------------------------------------- icons

    _gicon(name) {
        const path = `${this._extension.path}/icons/${name}.svg`;
        return Gio.icon_new_for_string(path);
    }

    _iconForAgent(agent) {
        return this._gicon(AGENT_ICON[agent] ?? GENERIC_ICON);
    }

    _stateStyle(state) {
        const color = STATE_COLOR[state];
        return color ? `color: ${color};` : '';
    }

    // St.Icon for a state: themed icons for question/done, vendored glyphs
    // for busy (shovel) / idle (zzz); tinted/dimmed via the vigia-state styles
    _statusIcon(state, size) {
        const props = {
            icon_size: size,
            style_class: `vigia-state vigia-state-${state}`,
            style: this._stateStyle(state),
            y_align: Clutter.ActorAlign.CENTER,
        };
        if (STATE_ICON_THEME[state])
            return new St.Icon({icon_name: STATE_ICON_THEME[state], ...props});
        return new St.Icon({gicon: this._gicon(STATE_ICON_FILE[state]), ...props});
    }

    // -------------------------------------------------------------- menu

    _buildMenu() {
        const header = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const headerBox = new St.BoxLayout({style_class: 'vigia-row', x_expand: true});
        this._headerIcon = new St.Icon({
            gicon: this._gicon(BRAND_ICON), icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._headerTitle = new St.Label({
            text: 'VigIA', style_class: 'vigia-row-title',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._headerStatus = new St.Label({
            text: '', style_class: 'vigia-offline', x_expand: true,
            x_align: Clutter.ActorAlign.END, y_align: Clutter.ActorAlign.CENTER,
        });
        headerBox.add_child(this._headerIcon);
        headerBox.add_child(this._headerTitle);
        headerBox.add_child(this._headerStatus);
        header.add_child(headerBox);
        this.menu.addMenuItem(header);

        this._listSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._listSection);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._awakeSwitch = new PopupMenu.PopupSwitchMenuItem(
            'Keep computer awake while agents work',
            this._settings.get_boolean('keep-awake'));
        this._awakeSwitch.connect('toggled', item => {
            this._settings.set_boolean('keep-awake', item.state);
        });
        this.menu.addMenuItem(this._awakeSwitch);

        const prefs = new PopupMenu.PopupMenuItem('VigIA Preferences…');
        prefs.connect('activate', () => this._extension.openPreferences());
        this.menu.addMenuItem(prefs);
    }

    // ------------------------------------------------------------ render

    _sorted() {
        const now = Date.now() / 1000;
        return Object.entries(this._agents)
            .map(([id, e]) => ({id, ...e, _age: Math.max(0, now - (e.updated || now))}))
            .sort((a, b) =>
                (STATE_ORDER[a.state] - STATE_ORDER[b.state]) ||
                ((b.updated || 0) - (a.updated || 0)));
    }

    _render() {
        const entries = this._sorted();
        const active = entries.filter(e => e.state !== 'idle');
        const showIdle = this._settings.get_boolean('show-idle');

        // ---- panel icons
        for (const child of this._panelBox.get_children())
            child.destroy();
        this._busyIcons = [];

        if (active.length === 0) {
            this.visible = showIdle;
            if (showIdle) {
                const dim = new St.Icon({
                    gicon: this._gicon(BRAND_ICON), icon_size: 16,
                    style_class: 'vigia-dim',
                });
                this._panelBox.add_child(dim);
            }
        } else {
            this.visible = true;
            active.slice(0, MAX_PANEL_ICONS).forEach(e =>
                this._panelBox.add_child(this._panelEntry(e)));
            if (active.length > MAX_PANEL_ICONS) {
                this._panelBox.add_child(new St.Label({
                    text: `+${active.length - MAX_PANEL_ICONS}`,
                    style_class: 'vigia-overflow',
                    y_align: Clutter.ActorAlign.CENTER,
                }));
            }
        }

        // ---- menu list
        this._listSection.removeAll();
        this._headerStatus.text = this._batteryCritical()
            ? 'battery critical — agents stopped'
            : (this._daemonOk ? '' : 'watcher offline');
        if (entries.length === 0) {
            const empty = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
            empty.add_child(new St.Label({
                text: this._daemonOk
                    ? 'No agents seen yet — start one!'
                    : 'Waiting for the vigia daemon…',
                style_class: this._daemonOk ? 'vigia-row-sub' : 'vigia-offline',
            }));
            this._listSection.addMenuItem(empty);
        } else {
            for (const e of entries)
                this._listSection.addMenuItem(this._menuRow(e));
        }

        this._updatePulse();
        this._updateInhibit();
    }

    _panelEntry(e) {
        const box = new St.BoxLayout({style_class: 'vigia-agent'});
        const style = this._iconStyle(e);
        const icon = new St.Icon({
            gicon: this._iconForAgent(e.agent), icon_size: 16, style,
        });
        box.add_child(icon);

        if (e.state === 'busy' && !this._fadedDone.has(e.id)) {
            icon.add_style_class_name('vigia-pulsing');
            if (this._pulseOn)
                icon.add_style_class_name('vigia-dim-phase');
            this._busyIcons.push(icon);
        } else if (e.state === 'done' && this._fadedDone.has(e.id)) {
            icon.add_style_class_name('vigia-dim');
        }
        if (e.state === 'question' || (e.state === 'done' && !this._fadedDone.has(e.id)))
            box.add_child(this._statusIcon(e.state, 11));
        this._scheduleFade(e.id, e.state);
        return box;
    }

    _iconStyle(e) {
        if (e.state === 'done' && this._fadedDone.has(e.id))
            return '';
        return this._stateStyle(e.state);
    }

    _menuRow(e) {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const box = new St.BoxLayout({style_class: 'vigia-row', x_expand: true});

        const icon = new St.Icon({
            gicon: this._iconForAgent(e.agent), icon_size: 18,
            style: this._stateStyle(e.state),
            y_align: Clutter.ActorAlign.CENTER,
        });
        box.add_child(icon);

        const text = new St.BoxLayout({vertical: true});
        const name = e.agent === 'claude' ? 'Claude Code'
            : e.agent === 'opencode' ? 'OpenCode'
            : e.agent.charAt(0).toUpperCase() + e.agent.slice(1);
        text.add_child(new St.Label({
            text: `${name} — ${e.title || 'session'}`,
            style_class: 'vigia-row-title',
        }));
        const sub = [e.project, e.model].filter(Boolean).join(' · ');
        if (sub)
            text.add_child(new St.Label({text: sub, style_class: 'vigia-row-sub'}));
        if (e.detail && e.state === 'question')
            text.add_child(new St.Label({text: e.detail, style_class: 'vigia-row-sub'}));
        box.add_child(text);

        // status glyph instead of the old text label; readable via screen readers
        const state = this._statusIcon(e.state, 14);
        state.x_expand = true;
        state.x_align = Clutter.ActorAlign.END;
        state.accessible_name = STATE_LABEL[e.state] ?? e.state;
        box.add_child(state);

        const age = new St.Label({
            text: formatAge(e._age), style_class: 'vigia-age',
            y_align: Clutter.ActorAlign.CENTER,
        });
        box.add_child(age);

        item.add_child(box);
        return item;
    }

    // ------------------------------------------------------------ motion

    _updatePulse() {
        const wantTimer = this._busyIcons.length > 0;
        if (wantTimer && !this._pulseTimer) {
            this._pulseTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
                this._pulseOn = !this._pulseOn;
                for (const icon of this._busyIcons) {
                    if (this._pulseOn)
                        icon.add_style_class_name('vigia-dim-phase');
                    else
                        icon.remove_style_class_name('vigia-dim-phase');
                }
                return GLib.SOURCE_CONTINUE;
            });
        } else if (!wantTimer && this._pulseTimer) {
            GLib.source_remove(this._pulseTimer);
            this._pulseTimer = null;
        }
    }

    _scheduleFade(id, state) {
        if (state !== 'done' || this._fadedDone.has(id) || this._fadeTimeouts.has(id))
            return;
        const secs = this._settings.get_int('done-fade-seconds');
        const src = GLib.timeout_add(GLib.PRIORITY_DEFAULT, secs * 1000, () => {
            this._fadeTimeouts.delete(id);
            this._fadedDone.add(id);
            this._render();
            return GLib.SOURCE_REMOVE;
        });
        this._fadeTimeouts.set(id, src);
    }

    _clearFade(id) {
        const src = this._fadeTimeouts.get(id);
        if (src) {
            GLib.source_remove(src);
            this._fadeTimeouts.delete(id);
        }
        this._fadedDone.delete(id);
    }

    // ------------------------------------------------------------ D-Bus

    _connectProxy() {
        if (this._proxy)
            return;
        try {
            this._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                null, BUS_NAME, OBJ_PATH, IFACE, null);
        } catch (e) {
            log(`vigia: failed to create proxy: ${e}`);
            return;
        }
        this._sigId = this._proxy.connect('g-signal', (p, iface, signal, params) => {
            try {
                const [id, payload] = params.deepUnpack();
                if (signal === 'AgentChanged') {
                    this._clearFade(id);
                    this._agents[id] = JSON.parse(payload);
                } else if (signal === 'AgentGone') {
                    this._clearFade(id);
                    delete this._agents[id];
                } else {
                    return;
                }
                this._render();
            } catch (e) {
                log(`vigia: bad signal payload: ${e}`);
            }
        });
        this._ownerId2 = this._proxy.connect('notify::g-name-owner', () => {
            if (this._proxy.g_name_owner)
                this._seed();
        });
        this._seed();
    }

    _seed() {
        try {
            const res = this._proxy.call_sync('List', null,
                Gio.DBusCallFlags.NONE, 2000, null);
            const [payload] = res.deepUnpack();
            const data = JSON.parse(payload);
            this._agents = data.agents ?? {};
            this._daemonOk = true;
        } catch (e) {
            this._daemonOk = false;
        }
        this._render();
    }

    _onVanished() {
        this._daemonOk = false;
        this._agents = {};
        // stale fade timers would fire against an empty table otherwise
        for (const src of this._fadeTimeouts.values())
            GLib.source_remove(src);
        this._fadeTimeouts.clear();
        this._fadedDone.clear();
        this._render();
    }

    // ------------------------------------------------------- keep awake

    _ensureSystemBus() {
        if (this._systemBus || this._systemBusFailed)
            return;
        try {
            this._systemBus = Gio.bus_get_sync(Gio.BusType.SYSTEM, null);
        } catch (e) {
            log(`vigia: no system bus, keep-awake disabled: ${e}`);
            this._systemBusFailed = true;
        }
    }

    _takeLogindInhibit() {
        if (this._logindFd != null)
            return;
        try {
            const [res, fdList] = this._systemBus.call_with_unix_fd_list_sync(
                'org.freedesktop.login1', '/org/freedesktop/login1',
                'org.freedesktop.login1.Manager', 'Inhibit',
                new GLib.Variant('(ssss)',
                    ['idle:sleep:handle-lid-switch', 'VigIA', 'AI agent working', 'block']),
                null, Gio.DBusCallFlags.NONE, -1, null, null);
            const [idx] = res.deepUnpack();
            this._logindFd = fdList.get(idx);
        } catch (e) {
            log(`vigia: logind inhibit failed: ${e}`);
        }
    }

    _releaseLogindInhibit() {
        if (this._logindFd == null)
            return;
        try {
            GLib.close(this._logindFd);
        } catch (e) { /* already closed */ }
        this._logindFd = null;
    }

    // The saved idle-delay is persisted to disk for the duration of the hold:
    // the logind inhibitor self-heals on a shell crash (the fd closes), but a
    // setting written to 0 would stay 0 forever, disabling blanking permanently.
    // enable() → _recoverBlankHold() restores the saved value if we crashed.
    _idleDelayStatePath() {
        return GLib.build_filenamev([GLib.get_user_state_dir(), 'vigia', 'idle-delay']);
    }

    _recoverBlankHold() {
        let saved = null;
        try {
            const [ok, bytes] = GLib.file_get_contents(this._idleDelayStatePath());
            if (ok)
                saved = Number.parseInt(new TextDecoder().decode(bytes).trim(), 10);
        } catch {
            return;   // no state file: last disable() was clean
        }
        try {
            GLib.unlink(this._idleDelayStatePath());
        } catch {
            // best effort
        }
        if (!Number.isInteger(saved) || saved <= 0)
            return;
        try {
            const settings = new Gio.Settings({schema_id: 'org.gnome.desktop.session'});
            settings.set_uint('idle-delay', saved);
            log(`vigia: recovered idle-delay=${saved}s after an unclean shutdown`);
        } catch (e) {
            log(`vigia: could not recover idle-delay: ${e}`);
        }
    }

    _takeBlankHold() {
        if (!this._idleDelaySettings) {
            try {
                this._idleDelaySettings =
                    new Gio.Settings({schema_id: 'org.gnome.desktop.session'});
            } catch (e) {
                return;
            }
        }
        if (this._savedIdleDelay == null) {
            this._savedIdleDelay = this._idleDelaySettings.get_uint('idle-delay');
            try {
                GLib.file_set_contents(this._idleDelayStatePath(),
                    String(this._savedIdleDelay));
            } catch (e) {
                log(`vigia: could not persist idle-delay state: ${e}`);
            }
        }
        this._idleDelaySettings.set_uint('idle-delay', 0);
    }

    _releaseBlankHold() {
        if (this._savedIdleDelay == null)
            return;
        this._idleDelaySettings.set_uint('idle-delay', this._savedIdleDelay);
        this._savedIdleDelay = null;
        try {
            GLib.unlink(this._idleDelayStatePath());
        } catch {
            // best effort
        }
    }

    // ------------------------------------------------- critical battery

    _setupPowerMonitor() {
        if (this._powerProxy || this._powerFailed)
            return;
        try {
            this._powerProxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                null, 'org.freedesktop.UPower',
                '/org/freedesktop/UPower/devices/DisplayDevice',
                'org.freedesktop.UPower.Device', null);
        } catch (e) {
            log(`vigia: no UPower, critical-battery guard disabled: ${e}`);
            this._powerFailed = true;
            return;
        }
        this._powerSigId = this._powerProxy.connect('g-properties-changed',
            () => this._onPowerChanged());
    }

    // true when the battery is critically low — the only condition under
    // which VigIA surrenders and lets the machine sleep/power off
    _batteryCritical() {
        if (!this._powerProxy || !this._powerProxy.g_name_owner)
            return false;
        try {
            const read = key => this._powerProxy.get_cached_property(key)?.unpack();
            const warning = read('WarningLevel') ?? 0;   // 4 = critical, 5 = action
            const state = read('State') ?? 0;            // 2 = discharging
            const pct = read('Percentage') ?? 100;
            if (warning >= 4)
                return true;
            return state === 2 && pct <= 3;              // UPower-less fallback
        } catch (e) {
            return false;
        }
    }

    // --------------------------------------------- critical battery stop

    _onPowerChanged() {
        const critical = this._batteryCritical();
        const busy = this._sorted().some(e => e.state === 'busy' || e.state === 'question');
        if (critical && !this._wasCritical && busy)
            this._emergencyStop();
        this._wasCritical = critical;
        this._updateInhibit(true);
        this._render();
    }

    _emergencyStop() {
        if (!this._proxy || !this._proxy.g_name_owner) {
            Main.notify('VigIA — battery critically low',
                'The watcher daemon is offline, so agents could not be stopped automatically. Save your work now.');
            return;
        }
        this._proxy.call('StopAll', new GLib.Variant('(s)', ['battery-critical']),
            Gio.DBusCallFlags.NONE, 20000, null, (proxy, res) => {
                let detail;
                try {
                    const [jsonStr] = proxy.call_finish(res).deepUnpack();
                    const s = JSON.parse(jsonStr);
                    const st = s.stopped ?? {};
                    const parts = [];
                    if (st.opencode?.length)
                        parts.push(`${st.opencode.length} OpenCode session(s) interrupted`);
                    if (st.claude?.length)
                        parts.push(`${st.claude.length} Claude Code session(s) stopped`);
                    if (st.generic?.length)
                        parts.push(`${st.generic.length} other agent(s) stopped`);
                    detail = parts.length
                        ? `Stopped ${parts.join(' · ')}. Unsaved conversation state is preserved.`
                        : 'No actively working agent found to stop.';
                } catch (e) {
                    detail = 'Could not stop the agents automatically — check their windows.';
                }
                Main.notify('VigIA — battery critically low 🔋',
                    `${detail} Keep-awake released so the system can save itself. Plug in the charger to resume.`);
            });
    }

    _updateInhibit(force = false) {
        const want = this._settings.get_boolean('keep-awake') &&
            this._sorted().some(e => e.state === 'busy' || e.state === 'question') &&
            !this._batteryCritical();
        if (!force && want === this._inhibited)
            return;
        this._ensureSystemBus();
        if (!this._systemBus)
            return;
        this._inhibited = want;
        if (want) {
            this._takeLogindInhibit();
            this._takeBlankHold();
            // logind refused the inhibitor: clear the flag so the next
            // _updateInhibit() retries instead of assuming we are held
            if (this._logindFd == null)
                this._inhibited = false;
        } else {
            this._releaseLogindInhibit();
            this._releaseBlankHold();
        }
    }

    // ---------------------------------------------------------- cleanup

    destroy() {
        if (this._keepAwakeId) {
            this._settings.disconnect(this._keepAwakeId);
            this._keepAwakeId = null;
        }
        if (this._showIdleId) {
            this._settings.disconnect(this._showIdleId);
            this._showIdleId = null;
        }
        if (this._ownerId != null) {
            Gio.bus_unwatch_name(this._ownerId);
            this._ownerId = null;
        }
        if (this._proxy && this._sigId != null) {
            this._proxy.disconnect(this._sigId);
            this._sigId = null;
        }
        if (this._proxy && this._ownerId2 != null) {
            this._proxy.disconnect(this._ownerId2);
            this._ownerId2 = null;
        }
        if (this._powerProxy && this._powerSigId != null) {
            this._powerProxy.disconnect(this._powerSigId);
            this._powerSigId = null;
        }
        this._powerProxy = null;
        if (this._pulseTimer) {
            GLib.source_remove(this._pulseTimer);
            this._pulseTimer = null;
        }
        for (const src of this._fadeTimeouts.values())
            GLib.source_remove(src);
        this._fadeTimeouts.clear();

        // release the inhibitor and blank hold — never leak them
        this._releaseLogindInhibit();
        this._releaseBlankHold();
        this._inhibited = false;
        super.destroy();
    }
});

export default class VigiaExtension extends Extension {
    enable() {
        this._indicator = new VigiaIndicator(this);
        Main.panel.addToStatusArea('vigia', this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}

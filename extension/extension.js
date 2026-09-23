// Vigía — GNOME Shell extension
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

// org.gnome.SessionManager.Inhibit flags — same values Caffeine uses
// (prevents screen blanking and suspend).
const INHIBIT_FLAGS = 12;

const STATE_ORDER = {busy: 0, question: 1, done: 2, idle: 3};
const STATE_COLOR = {busy: null, question: '#f6d32d', done: '#33d17a', idle: null};
const STATE_LABEL = {busy: 'working', question: 'needs you', done: 'done', idle: 'idle'};
const AGENT_ICON = {
    opencode: 'vigia-oc-symbolic',
    claude: 'vigia-cc-symbolic',
    codex: 'vigia-cx-symbolic',
};
const GENERIC_ICON = 'vigia-generic-symbolic';
const BRAND_ICON = 'vigia-symbolic';
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
        super._init(0.0, 'Vigía', false);
        this._extension = extension;
        this._settings = extension.getSettings();
        this._agents = {};
        this._daemonOk = false;
        this._busyIcons = [];
        this._pulseOn = false;
        this._fadedDone = new Set();
        this._fadeTimeouts = new Map();
        this._cookie = null;
        this._proxy = null;
        this._sigId = null;
        this._ownerId = null;

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

    // -------------------------------------------------------------- menu

    _buildMenu() {
        const header = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const headerBox = new St.BoxLayout({style_class: 'vigia-row', x_expand: true});
        this._headerIcon = new St.Icon({
            gicon: this._gicon(BRAND_ICON), icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._headerTitle = new St.Label({
            text: 'Vigía', style_class: 'vigia-row-title',
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

        const prefs = new PopupMenu.PopupMenuItem('Vigía Preferences…');
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
        this._headerStatus.text = this._daemonOk ? '' : 'watcher offline';
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
        if (e.state === 'question') {
            box.add_child(new St.Label({
                text: '!', style_class: 'vigia-badge vigia-badge-q',
                y_align: Clutter.ActorAlign.CENTER,
            }));
        } else if (e.state === 'done' && !this._fadedDone.has(e.id)) {
            box.add_child(new St.Label({
                text: '✓', style_class: 'vigia-badge vigia-badge-d',
                y_align: Clutter.ActorAlign.CENTER,
            }));
        }
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
            : e.agent === 'opencode' ? 'OpenCode' : e.agent;
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

        const state = new St.Label({
            text: STATE_LABEL[e.state] ?? e.state,
            style_class: `vigia-state vigia-state-${e.state}`,
            x_expand: true, x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.CENTER,
        });
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
        this._render();
    }

    // ------------------------------------------------------- keep awake

    _ensureSessionProxy() {
        if (this._session || this._sessionFailed)
            return;
        try {
            this._session = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                null, 'org.gnome.SessionManager', '/org/gnome/SessionManager',
                'org.gnome.SessionManager', null);
        } catch (e) {
            log(`vigia: no session manager, keep-awake disabled: ${e}`);
            this._sessionFailed = true;
        }
    }

    _updateInhibit() {
        const want = this._settings.get_boolean('keep-awake') &&
            this._sorted().some(e => e.state === 'busy' || e.state === 'question');
        this._ensureSessionProxy();
        if (!this._session)
            return;
        if (want && this._cookie == null) {
            this._session.InhibitRemote('Vigía', 0, 'AI agent working', INHIBIT_FLAGS,
                res => {
                    try {
                        const [cookie] = res;
                        this._cookie = cookie;
                    } catch (e) {
                        log(`vigia: inhibit failed: ${e}`);
                    }
                });
        } else if (!want && this._cookie != null) {
            this._session.UninhibitRemote(this._cookie);
            this._cookie = null;
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
        if (this._pulseTimer) {
            GLib.source_remove(this._pulseTimer);
            this._pulseTimer = null;
        }
        for (const src of this._fadeTimeouts.values())
            GLib.source_remove(src);
        this._fadeTimeouts.clear();

        // never leave an inhibitor behind
        if (this._cookie != null && this._session) {
            try {
                this._session.UninhibitRemote(this._cookie);
            } catch (e) { /* session is going away anyway */ }
            this._cookie = null;
        }
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

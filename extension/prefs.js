// VigIA — preferences window

import Adw from 'gi://Adw';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';

export default class VigiaPreferences extends Adw.PreferencesWindow {
    static {
        GObject.registerClass(this);
    }

    constructor(extension) {
        super({});
        this._settings = extension.getSettings();

        const page = new Adw.PreferencesPage();
        this.add(page);

        // ---- general --------------------------------------------------
        const general = new Adw.PreferencesGroup({title: 'General'});
        page.add(general);

        const showIdle = new Adw.SwitchRow({
            title: 'Show watcher when all agents are idle',
            subtitle: 'Otherwise the indicator hides until an agent starts working',
        });
        general.add(showIdle);

        const keepAwake = new Adw.SwitchRow({
            title: 'Keep computer awake while agents work',
            subtitle: 'Inhibits screen blanking and suspend while any agent is busy or waiting for you',
        });
        general.add(keepAwake);

        const fade = Adw.SpinRow.new_with_range(5, 60, 1);
        fade.title = "Seconds the 'done' badge stays lit";
        fade.subtitle = 'After this, a finished agent dims back in the top bar';
        general.add(fade);

        this._settings.bind('show-idle', showIdle, 'active', Gio.SettingsBindFlags.DEFAULT);
        this._settings.bind('keep-awake', keepAwake, 'active', Gio.SettingsBindFlags.DEFAULT);
        this._settings.bind('done-fade-seconds', fade, 'value', Gio.SettingsBindFlags.DEFAULT);

        // ---- sources --------------------------------------------------
        const sources = new Adw.PreferencesGroup({
            title: 'Watched agents',
            description: 'Changes apply to the vigia daemon immediately',
        });
        page.add(sources);

        const oc = new Adw.SwitchRow({
            title: 'OpenCode',
            subtitle: 'Busy/idle/questions detected via the background service',
        });
        sources.add(oc);

        const cc = new Adw.SwitchRow({
            title: 'Claude Code',
            subtitle: 'States pushed by the vigia hook reporter installed in Claude Code',
        });
        sources.add(cc);

        const generic = new Adw.SwitchRow({
            title: 'Other agents by process name',
            subtitle: 'Shows "working" while any matching process is alive',
        });
        sources.add(generic);

        const procs = new Adw.EntryRow({title: 'Process names (comma-separated)'});
        sources.add(procs);

        const poll = Adw.SpinRow.new_with_range(2, 30, 1);
        poll.title = 'OpenCode poll interval (seconds)';
        sources.add(poll);

        this._settings.bind('source-opencode', oc, 'active', Gio.SettingsBindFlags.DEFAULT);
        this._settings.bind('source-claude', cc, 'active', Gio.SettingsBindFlags.DEFAULT);
        this._settings.bind('source-generic', generic, 'active', Gio.SettingsBindFlags.DEFAULT);
        this._settings.bind('generic-processes', procs, 'text', Gio.SettingsBindFlags.DEFAULT);
        this._settings.bind('poll-interval', poll, 'value', Gio.SettingsBindFlags.DEFAULT);

        // ---- about ----------------------------------------------------
        const about = new Adw.PreferencesGroup({title: 'About'});
        page.add(about);
        about.add(new Adw.ActionRow({
            title: 'VigIA 1.0',
            subtitle: 'The lookout for your AI agents · daemon: systemctl --user status vigia',
        }));
    }
}

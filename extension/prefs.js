// VigIA — preferences window
//
// GNOME 47+ contract: the shell's prefs dialog instantiates the default
// export with the extension's metadata ({...metadata, dir, path}) and then
// awaits fillPreferencesWindow(dialogWindow). Extending ExtensionPreferences
// gives us getSettings() from metadata['settings-schema'].

import Adw from 'gi://Adw';
import Gio from 'gi://Gio';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class VigiaPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        const page = new Adw.PreferencesPage();
        window.add(page);

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
            subtitle: 'Ignores the lid, blocks idle sleep and auto-suspend on AC and battery. Releases automatically on critically low battery after stopping the agents',
        });
        general.add(keepAwake);

        const fade = Adw.SpinRow.new_with_range(5, 60, 1);
        fade.title = "Seconds the 'done' badge stays lit";
        fade.subtitle = 'After this, a finished agent dims back in the top bar';
        general.add(fade);

        settings.bind('show-idle', showIdle, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('keep-awake', keepAwake, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('done-fade-seconds', fade, 'value', Gio.SettingsBindFlags.DEFAULT);

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

        settings.bind('source-opencode', oc, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('source-claude', cc, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('source-generic', generic, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('generic-processes', procs, 'text', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('poll-interval', poll, 'value', Gio.SettingsBindFlags.DEFAULT);

        // ---- about ----------------------------------------------------
        const about = new Adw.PreferencesGroup({title: 'About'});
        page.add(about);
        // metadata 'version' is the EGO package version (assigned on upload,
        // not locally); the About row and the daemon carry version 1.0.
        about.add(new Adw.ActionRow({
            title: 'VigIA 1.0',
            subtitle: 'The lookout for your AI agents · daemon: systemctl --user status vigia',
        }));
    }
}

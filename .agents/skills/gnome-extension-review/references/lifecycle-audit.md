# Lifecycle Audit — How to Verify enable()/disable() Symmetry

The core of an extension review. The golden rule: **`disable()` is `enable()`'s
mirror.** Every resource acquired between import and `enable()` must be released
in `disable()`, because GNOME Shell can call `enable()`/`disable()` repeatedly
(session lock/unlock, mode changes) and at shutdown.

## 1. What may happen during initialization (45+)

Module import + `constructor()` (= `init()` in legacy code) is **static-only**
(rule R1):

- ✅ Allowed: static data structures (`RegExp`, `Map` of constants),
  `initTranslations()`, reading `metadata`, `this.getSettings()` is *NOT* static
  (creates a `Gio.Settings` GObject) — treat as violation unless genuinely
  released in `disable()`.
- ❌ Not allowed: creating GObjects (`St.*`, `Gio.Settings`, `Soup.Session`,
  `GObject.registerClass` *instances*), `connect()`, main loop sources, any
  mutation of `Main.*` / GNOME Shell.

## 2. Audit procedure

1. **Inventory creation sites.** Scan every module for:
   - `.connect(` / `.connectObject(` (signals)
   - `timeout_add`, `timeout_add_seconds`, `idle_add`, `setTimeout`,
     `setInterval` (sources)
   - `new St.`, `new Gtk.`, `new Adw.`, `new Gio.`, `new Soup.` (objects/widgets)
   - mutations of shared Shell objects: `Main.panel.*add*`,
     `Main.panel.addToStatusArea`, `Main.layoutManager.addChrome`,
     `add_child` on imported Shell widgets, `Main.` property assignment,
     `InjectionManager.overrideMethod`, prototype patching
     (`Class.prototype.method =`), signal interception
   - GSettings: `bind(`, `connect('changed` on settings objects
   - D-Bus: `Gio.DBusExportedObject`, `Gio.bus_watch_name`, session-bus owns
2. **Inventory cleanup sites** in `disable()` (and in custom `destroy()` /
   `vfunc_destroy` for owned widgets).
3. **Match 1:1.** Each creation needs a corresponding release:

   | Created in enable/constructor | Required cleanup in disable/destroy |
   | --- | --- |
   | `obj.connect(id = obj.connect(...))` | `obj.disconnect(id)` (id nulled) |
   | `obj.connectObject(sig, cb, owner)` | `obj.disconnectObject(owner)` **or** owner is destroyed |
   | `GLib.timeout_add*` / `idle_add` → `this._srcId` | `GLib.Source.remove(this._srcId)` + null — even if callback returns `SOURCE_REMOVE` |
   | `setTimeout` / `setInterval` | `clearTimeout` / `clearInterval` |
   | widget added to own container | parent's `destroy()` cascades — but top-level widget itself needs `destroy()` |
   | item added to Shell (`Main.panel.addToStatusArea`, `quickSettingsItems.push`, `add_child` on Shell widgets) | explicit removal (`remove_child`, splice, `indicator.destroy()` …) |
   | `InjectionManager.overrideMethod` | `restoreMethod(...)` or `manager.clear()` |
   | prototype patch / function override | restore original reference |
   | `settings.bind(...)` / settings signal | `settings.unbind(...)` / disconnect |
   | `Soup.Session`, `Gio.Cancellable` | `cancel()`, drop reference |
   | `Gio.DBus.*` watches/owned names | unwatch/unown |
   | stylesheet added via `St.ThemeContext` / `loadTheme` | restore previous theme |
   | `Main._backgroundActor`-style grabs, `pushModal` | corresponding pop/release |

4. **Check destroy() order** (custom classes): remove sources → disconnect
   signals → release child refs → `super.destroy()` **last**.
5. **Check re-entrancy.** `disable()` must tolerate being called at any time;
   `enable()` after `disable()` must not double-add. Removal must happen next to
   creation for repeatable code paths (timeout-before-creation pattern).
6. **Check per-class ownership.** Cleanup must live in the same class that
   created the resource (no spaghetti cleanup).

## 3. Typical leak signatures

- `connect(` present in a class, no `disconnect(` anywhere.
- `timeout_add` count > `GLib.Source.remove` count in a file.
- Widgets assigned to `Main.panel.statusArea[...]` never destroyed.
- `_destroyed` / `_enabled` boolean lifecycle flags (anti-pattern — best
  practices forbid them).
- Signal IDs stored but `disable()` disconnects only some of them.
- try-catch swallowing cleanup failures (anti-pattern).
- Cleanup in a different class than creation (spaghetti cleanup).
- Timeouts re-created on every settings change without removing the old source.

## 4. Correct reference skeleton (GNOME 45+)

```js
import GObject from 'gi://GObject';
import St from 'gi://St';
import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const ExampleWidget = GObject.registerClass(
class ExampleWidget extends St.Button {
    destroy() {
        if (this._timeoutId) {
            GLib.Source.remove(this._timeoutId);
            this._timeoutId = null;
        }
        super.destroy();                    // last step
    }
});

export default class ExampleExtension extends Extension {
    enable() {
        this._widget = new ExampleWidget();
        this._signalId = this._widget.connect('clicked',
            () => this._onClick());
        // ...
    }

    disable() {
        this._widget.disconnect(this._signalId);
        this._signalId = null;
        this._widget.destroy();
        this._widget = null;
    }
}
```

## 5. `unlock-dialog` checklist (when `session-modes` includes it)

- `disable()` has a comment explaining why `unlock-dialog` is needed (rule R18).
- Keyboard-event signals are disconnected while the screen is locked.
- The extension does not disable selectively (partial cleanup depending on
  session mode is forbidden).
- The extension tolerates `disable()` + `enable()` cycles on mode changes.

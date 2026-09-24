# GNOME Shell js/ui Map + Live API Verification

Ground truth for verifying Shell APIs. Import base paths (GNOME 45+ ES modules):

```js
// Shell process (extension.js and its modules):
import Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

// Entry points must be default exports:
export default class MyExtension extends Extension { ... }

// Own modules: relative imports (extension dir)
import {Helper} from './helper.js';
```

## main.js singletons (imported as `Main`)

| Singleton | Purpose / typical extension use |
| --- | --- |
| `Main.panel` | Top bar; `addToStatusArea()`, `statusArea[uuid]`, `statusArea.quickSettings` |
| `Main.sessionMode` | current session mode (`user`, `unlock-dialog`, …); `connect('updated')` |
| `Main.wm` | window manager (`addKeybinding`-related signals, `switchWorkspace`) |
| `Main.overview` | overview; `connect('showing'/'hiding')` |
| `Main.messageTray` | notifications; `Main.notify(title, body)` |
| `Main.layoutManager` | chrome (`addChrome`/`removeChrome`), monitors, uiGroup |
| `Main.uiGroup` | top-level actor container |
| `Main.keyboard` | on-screen keyboard |
| `Main.osdWindowManager` | on-screen display popups |
| `Main.componentManager` | component provider registration |

## Key js/ui modules

| Module | Exports extensions commonly use |
| --- | --- |
| `panelMenu.js` | `Button`, `ButtonBox` — panel indicator buttons |
| `popupMenu.js` | `PopupMenu`, `PopupMenuBase`, `PopupMenuSection`, `PopupMenuItem`, `PopupSwitchMenuItem`, `PopupImageMenuItem`, `PopupSeparatorMenuItem`, `PopupSubMenuMenuItem`, `PopupMenuManager`, `Ornament`, `Switch`, `arrowIcon()` |
| `quickSettings.js` | `SystemIndicator`, `QuickSettingsItem`, `QuickToggle`, `QuickMenuToggle`, `QuickSlider` (since GNOME 43) |
| `modalDialog.js` | `ModalDialog`, `State` |
| `messageTray.js` | `Notification`, `Source`, `Urgency`, `NotificationDestroyedReason` |
| `dialog.js` | `Dialog`, `DialogState` |
| `slider.js` | `Slider` |
| `barLevel.js` | `BarLevel` (progress bars) |
| `checkBox.js` | `CheckBox` |
| `boxpointer.js` | `BoxPointer` |
| `dnd.js` | drag-and-drop helpers |
| `grabHelper.js` | `GrabHelper` (modal grabs) |
| `switcherPopup.js`, `altTab.js` | app/window switchers |
| `search.js`, `remoteSearch.js` | search integration |
| `extensionSystem.js`, `extensionDownloader.js` | extension manager internals (do not touch — R8) |
| `js/ui/status/*` | network, bluetooth, nightLight, brightness… — best examples of QuickSettings toggles |
| `js/ui/components/*` | autoloaded components |

## Live verification (authoritative, works without JS rendering)

```sh
# List js/ui files (add &recursive=true for subdirs)
curl -s "https://gitlab.gnome.org/api/v4/projects/GNOME%2Fgnome-shell/repository/tree?path=js/ui&ref=main&per_page=100"

# Read a file on main (or pin: ref=49.0 tag / stable branch name)
curl -s "https://gitlab.gnome.org/GNOME/gnome-shell/-/raw/main/js/ui/panelMenu.js"
```

Pin to the user's target GNOME version when checking version-sensitive APIs
(tags are release versions like `49.0`).

## gjs-docs.gnome.org JSON API (DevDocs backend)

```sh
# All libraries (slug list)
curl -sL "https://gjs-docs.gnome.org/docs.json"

# Entry index for a library
curl -s "https://gjs-docs.gnome.org/docs/<slug>~<version>/index.json"

# Entry content: db.json, key = entry path (HTML fragment)
curl -s "https://gjs-docs.gnome.org/docs/<slug>~<version>/db.json"
```

Relevant slugs: `st17~17`, `clutter17~17`, `meta17~17`, `shell17~17`,
`glib20~2.0`, `gio20~2.0`, `giounix20~2.0`, `gobject20~2.0`, `adw1~1`,
`gtk40~4.0`, `soup30~3.0`, `gjs` (Cairo/System/Encoding docs), `nm10~1.0`.
Example: `https://gjs-docs.gnome.org/docs/glib20~2.0/index.json`, then look up
`glib.timeout_add` in that library's `db.json`.

Human-readable citation URLs: `https://gjs-docs.gnome.org/<slug>/<entry>` (e.g.
`https://gjs-docs.gnome.org/glib20/glib.timeout_add`).

## Version-sensitivity notes (45+ scope)

- **45**: ESModules mandatory; `Extension`/`ExtensionPreferences` classes
  replace `init()`/`enable()` functions; `InjectionManager` available.
- **46–51**: read the matching porting guide
  (`https://gjs.guide/extensions/upgrading/gnome-shell-<NN>.html`) when the
  code targets or claims those versions — e.g. 48/49/50/51 each moved or
  changed several `js/ui` APIs.
- When unsure whether an API exists in the target version, grep the pinned raw
  file (`ref=<tag>`) — never trust memory alone.

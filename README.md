# 🦀 Crabture — Yet Another Screenshot Tool (But Make It Crab)

**Crabture** is a ShareX-style screenshot tool for Debian, Kali and other Linux desktops. Yes, there are already a hundred of these. No, none of them were *this* crab-themed.

It captures your screen (X11 *and* Wayland, because apparently we can't have nice things in just one), lets you scribble on it, makes it look professional with effects, and then manages the pile of PNGs you will inevitably accumulate. Built with **PyQt6**, held together by optimism and an unreasonable number of throwaway test scripts that live outside the repo (so, no, there is no test suite. Yet.).

![Crabture Logo](assets/logo.png)

## ✨ Features (a.k.a. "Things We Did Instead of Sleeping")

### 📷 Screen capture
*Because Print Screen alone was apparently too easy.*

- **Crabture's own region selector is the default** (a frozen-screen overlay with ✔/✖ buttons, a pixel loupe and size readout). Prefer your desktop's own tool (GNOME's screenshot UI on Wayland, `xfce4-screenshooter` on Kali/Xfce, `gnome-screenshot` on Debian GNOME/X11)? Switch the tool in Configure ▸ Capture. If a tool is missing or fails, the next method takes over. Modes: region, **fullscreen**, **window** (on X11 and Windows the active window; on Wayland, where compositors will not say where windows are, Crabture outlines the window under your pointer by looking for its borders in the frozen picture: click to capture, `↑`/`↓` or the scroll wheel to step to a bigger or smaller candidate when the first guess is off, `Space` to draw a region instead), **monitor** (click the one you want when you have several), and **scrolling capture** (you scroll, Crabture stitches the frames, everyone pretends it was effortless).
- Works on **X11** and **Wayland**. On Wayland it talks to the xdg-desktop-portal like a well-behaved citizen (on GNOME the portal only grants a silent screenshot to a *focused window*, so Crabture asks through a tiny invisible helper window; if that is ever refused it falls back to the plain portal call and finally to GNOME's own picker); on X11 it can also use `maim`, `scrot` or ImageMagick `import` if you have them lying around. Window capture on Wayland opens the system picker, because the compositor said "no" to everything else.
- **Save wherever you like.** Configure ▸ Capture sets the folder (default `~/Downloads/Crabshots`), the file name pattern (`Crabture_%Y-%m-%d_%H-%M-%S`, with `{mode}` if you want it) and the format (PNG, JPEG, WebP). Files appear atomically, so the gallery never sees a half-written screenshot.
- **As many capture shortcuts as you want.** Each one takes one kind of screenshot and can have its own delay, its own folder and its own "then" (**just save it** — the default, with a notification — or open it in the editor). One for regions, one for the whole screen into `~/Work/shots`, one for a window after 3 seconds... knock yourself out.
- **Works from anywhere.** "Apply to desktop" in the same tab hands the shortcuts to **GNOME** or **Xfce**. It never touches a shortcut you already use, skips (and tells you about) any key that is taken, and removes only its own. On other desktops it shows the exact commands to bind by hand.
- If Crabture is already running, a shortcut is handed to that instance (instant, no second window). If not, a shortcut that only saves runs **without opening any window at all**.
- A shortcut, tray or command-line capture **never hides or pops up Crabture's own window**, so you can screenshot Crabture itself (region, full screen or window). Only captures started from Crabture's own buttons and menus hide it first (switch that off with *Hide the Crabture window while capturing* in Configure ▸ Capture).
- Options for a **delay** (our own window hides *before* the countdown, so you can set the scene), **copy to clipboard**, and **region capture on mouse release**.
- From the command line: `crabture --capture region|fullscreen|window|monitor|scroll [--delay S] [--folder DIR] [--after editor|save] [--clipboard]`.
- `crabture --register-hotkeys` applies the shortcuts saved in Configure ▸ Capture. It **never overwrites** one you already use. We're not monsters.

### 🖍 Annotation tools
*Seventeen of them. You'll use three.*

`Select`, `Arrow`, `Rectangle`, `Highlight`, `Pixelate`, `Blur`, `Text`, `Crop`, `Magnifier`, `Speech Balloon`, `Ellipse`, `Line`, freehand `Pen`, numbered `Step` markers, `Spotlight` (dims everything except the bit you want people to look at, i.e. the thing they'd have missed anyway), emoji `Sticker` (click the tool twice to pick one — yes, this is a professional tool), an `Eyedropper` and a `Hand` for panning.

- **The Eyedropper, now with a loupe.** Previously it was a crosshair and a prayer. Now you get an actual eyedropper cursor and a magnifying loupe that follows it: a pixel-exact zoom of whatever's under the pointer, a marker on the exact pixel you're about to pick, and a HEX/RGB readout *before* you click. Click to pick, or press and slide to fine-tune. `Esc` takes you back to the tool you were using. Revolutionary, we know. Colour pickers have only existed since the 80s.
- **Pixelate and Blur are live effects.** They re-render whatever's underneath them, with strength set by the **intensity slider** in the Properties panel (it also applies to a selected Pixelate/Blur item).
- Every annotation gets **its own layer**. The Layers panel lets you reorder, hide and delete them. Photoshop called; it's flattered.
- 8 preset colours, a custom picker, and an opacity slider, for when "red" isn't expressive enough.
- Hold `Shift` while drawing to constrain arrows to horizontal/vertical and rectangles/crops to squares. Geometry is hard.
- **Images open fitted to the window** and *stay* fitted when you resize things. Zoom manually and it respects your choice until you hit Fit. Shocking behaviour for software.

### ✨ Image effects (`Ctrl+E`)
*Make your bug report look like a startup landing page.*

Border, drop shadow, torn edge, text/logo watermark, and a padding + gradient background frame, with live preview and presets. Applying flattens the annotations into the image, and one undo brings everything back. Pretty *and* reversible. Don't get used to it.

### 🛡 Sensitive-data redaction, in two parts
*For those moments when you realise your screenshot contains your API key. Again.*

Needs `tesseract-ocr`. (Yes, you have to install it. Software doesn't write itself. Well, some of this did, but not that part.)

1. **Configure once** (`Configure ▸ Sensitive Data…`): choose detectors (emails, IPs, API keys, tokens, passwords, card numbers…), add your own regex patterns, set the OCR language, pick blur or pixelate and how hard, and decide whether you review findings first.
2. **Run any time** (`Edit ▸ Run Sensitive-Data Scan`, `Ctrl+Shift+F`, or the shield button): scans with your saved settings and shows what it found.

**Nothing is ever scanned or changed automatically.** The scan starts only when *you* click Run, and by default nothing is blurred until you review the findings and click *Apply redaction*. Skipping the review is an opt-in setting, for the brave and the reckless. Every redaction is a normal, undoable blur/pixelate layer, so a wrongly blurred cat photo is one `Ctrl+Z` away from freedom.

### ⚙ Configure (`Configure ▸ Preferences…`, `Ctrl+,`)
*Because you have opinions.*

- **Remap every tool key and menu shortcut**, with conflict checks, reset, and import/export. Yes, you can make `R` do something other than Rectangle. We won't ask why.
- **Per-tool defaults:** stroke widths, font and step sizes, sticker, blur strength, spotlight darkness, magnifier size, and more. Finally, a way to make every arrow *exactly* the thickness you like.
- Theme (**System / Light / Dark**) and capture defaults.
- The **gear button in the Properties panel** (top right) opens Configure ▸ Tools on the tool you are using; **Configure ▸ Screenshot Folder…** jumps straight to the folder setting (Capture tab). Save As and Open start in that folder too.

### 🧑‍🎨 A UI that doesn't look like 2004
- A **welcome page** with capture/open buttons and your recent captures, for when you launch the app with no plan, as one does.
- **Vector icons** (the emoji era of the toolbar is over; we are all healthier for it), a tool rail, a Properties panel, Layers and Gallery tabs.
- **Light and dark themes** that can follow your desktop, because you were going to ask.
- **Drag-and-drop** images or folders onto the window.
- **Always starts maximized.** Not a bug; a lifestyle choice.

### 🗂 Image gallery
*Your screenshots folder, but organised. Allegedly.*

- Watches a folder (default `~/Downloads/Crabshots`, or whatever you pass with `--directory`) and lists its images.
- **Four view styles**, because two was apparently an injustice:
  - **Grid:** thumbnail cards, with gutters that are finally the same on both sides (it took a lot of arguing with Qt).
  - **List:** thumbnail plus info.
  - **Details:** name, size, date and dimensions columns. Click a header to sort, like an adult.
  - **Compact:** names only, for people who think thumbnails are for show-offs.
- **Sort** by name (in *natural* order, so `Test_2` comes before `Test_10` and mathematicians can sleep), date modified, size or dimensions. Your view and sort choices are remembered.
- Right-click for Open, Power Rename, Open in File Manager, or Copy Path. **Select several files and "Open N selected files"** to open them as *one session*: the first is edited and the gallery lists only those files (right-click ▸ *Show the whole folder* to go back).
- The gallery always shows the folder you opened: a file opened from anywhere (File ▸ Open, drag and drop, the command line, "Open With") switches it to that file's folder; `--directory` or "Open Folder" picks one explicitly.

### ⚡ Power Rename (`Ctrl+R` on selected gallery items)
*Because `Screenshot from 2026-10-12 19-19-10 (3).png` is not a name, it's a cry for help.*

- **Five simple groups:** *Find & replace* (plain or regex; extra options under "More options") → *New name* (keep it, type one, or build it from a pattern; numbering options appear only when the name has a number) → *Add or remove text* → *Letter case* → *Tidy up*. Groups you don't use stay collapsed, and a small ON badge shows which ones are active.
- **Pattern parts** (click-to-add buttons in the dialog, or type them): `{name}` `{ext}` `{n:03}` `{date}` `{mtime:%Y-%m-%d}` `{parent}` `{w}` `{h}` `{size}` `{rand:6}` `{original}`. Numbering can follow name (natural sort), date, size or dimensions.
- Presets (built-in and your own), a live preview with conflict detection, **safe swaps** (rename a→b and b→a without anything exploding), and **Undo Last Rename** (`Edit ▸ Undo Last Rename`) for the moment you regret everything.

### 🐧 Linux integration
`./scripts/builder.sh --install` adds an application-menu entry (so Crabture shows up in "Open With" for common image types, several files at once) and two Nautilus scripts (right-click ▸ Scripts):

- **Open Directory with Crabture**
- **Edit with Crabture** — opens *all* selected images in one Crabture window (a single image shows its folder in the gallery). Two whole right-click entries. We're spoiling you.

The `.deb` ships the same scripts and its installer puts them into the Nautilus scripts folder of every user on the machine (and of users created later), so the entries are there right after `apt install`; `apt remove` takes back only the files that are Crabture's. Restart Nautilus once (`nautilus -q`) if the *Scripts* menu does not show up yet. However you open files (right-click, Open With, the command line), they all go to **one** Crabture window: launches that happen at the same moment, or while Crabture is already open, are merged into a single session instead of opening a window each. `crabture --install-integration` / `--remove-integration` do it (or undo it) by hand. (Thunar and other file managers: use "Open With ▸ Crabture".)

### 🪟 Windows integration
The installer (see [Windows](#-windows)) adds a Start-menu entry and, if you tick the boxes, **Edit with Crabture** on every image, **Open folder with Crabture** on folders, and a notification-area icon that starts at sign-in so the capture shortcuts work everywhere.

## 🚀 Installation
*Steps: three. Difficulty: you'll survive.* (On Windows, jump to [Windows](#-windows).)

1. Install the dependency (a virtual environment is recommended, but we're not your mum):
   ```bash
   pip install -r requirements.txt
   ```
2. Clone the repository somewhere **permanent**, then integrate it with your desktop:
   ```bash
   ./scripts/builder.sh --install
   ```
   This creates `~/.local/bin/crabture`, a `.desktop` entry, and the two Nautilus scripts. They point at the cloned source directory, so code changes apply immediately. That also means that if you move or delete the clone, Crabture will vanish in protest.
3. Make sure `~/.local/bin` is on your `PATH`.

Other builder commands, for the indecisive:

```bash
./scripts/builder.sh --build       # standalone executable via PyInstaller -> dist/crabture
./scripts/builder.sh --uninstall   # remove the launcher, .desktop entry and Nautilus scripts
./scripts/builder.sh --reinstall   # uninstall (including leftovers from old versions), then install cleanly
./scripts/builder.sh --help
```

`--build` needs PyInstaller; the script explains how to get it if it's missing, in a refreshingly non-judgemental tone.

## 🪟 Windows
*Same crab, different shell.* Windows 10 / 11, Python 3.10+.

**Run from source**

```powershell
py -3 -m pip install -r requirements.txt
py -3 main.py
```

**Build an installer** (PyInstaller bundle + Inno Setup `Setup.exe`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1              # -> dist\Crabture\Crabture.exe
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Installer   # -> dist\Crabture-<version>-Setup.exe
```

The installer needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).

What's different from Linux:

- **Capture** uses Qt's own screen grab and Crabture's frozen-screen selector; there is no portal or helper tool to install. *Active window* uses the Win32 foreground window (visible frame, no drop shadow), and *scrolling capture* can turn the mouse wheel for you.
- **Global shortcuts:** Windows has no shortcut database to write to, so **Configure ▸ Capture ▸ Apply to desktop** makes Crabture start in the notification area at sign-in (`crabture --background`) and register the keys itself (`RegisterHotKey`). A key another program already owns is reported in a notification instead of failing silently. *Remove from desktop* undoes it.
- **Notifications** are tray balloons / toasts.
- **OCR:** install Tesseract with `winget install UB-Mannheim.TesseractOCR`; Crabture finds it in `C:\Program Files\Tesseract-OCR`, or set `CRABTURE_TESSERACT` to the full path of `tesseract.exe`.
- **Files:** logs and the rename undo journal live in `%LOCALAPPDATA%\Crabture`; settings are in the registry (`HKCU\Software\YawnByte\Crabture`).

## 📦 Debian package
*For people who believe software should arrive via `apt` or not at all.*

Build and install a `.deb` on Debian, Kali, Ubuntu and friends:

```bash
./scripts/builder.sh --deb                       # -> dist/crabture_<version>_all.deb
sudo apt install ./dist/crabture_*_all.deb
sudo apt remove crabture                 # uninstall
```

The package installs the app to `/usr/lib/crabture`, a `crabture` launcher in `/usr/bin`, a desktop entry with capture actions, icons and a man page (`man crabture`, in case you enjoy reading).
It depends on `python3-pyqt6`; `tesseract-ocr` (the sensitive-data scanner), `xdotool` and `gsettings` (`libglib2.0-bin`) are recommended, and capture helpers like `maim`, `grim` or `gnome-screenshot` are suggested. Hotkeys are **never** registered automatically, because surprise keyboard shortcuts are rude.

Before publishing the package, set the maintainer in `packaging/build_deb.sh` and the upstream URL in `packaging/copyright`. The licence part is finally done, as you'll see below. The version comes from `crabture/version.py`.

> Don't install the `.deb` *and* run `--install`. The `.deb` puts `crabture` in `/usr/bin`, `--install` puts one in `~/.local/bin`, and your `PATH` will cheerfully pick the wrong one at the worst possible moment.

## ▶️ Usage

```bash
crabture                          # start with the default gallery folder
crabture path/to/image.png        # open an image in the editor (the gallery shows its folder)
crabture a.png b.png c.png        # open several images as one session (gallery lists just these)
crabture --directory ~/Pictures   # show a folder in the gallery (-d)
crabture --capture region         # take a screenshot straight away
crabture --capture window --delay 3
crabture --capture fullscreen --after save --folder ~/Work/shots   # no window, just a file (and a notification)
crabture --register-hotkeys       # give the shortcuts from Configure ▸ Capture to GNOME / Xfce
crabture --unregister-hotkeys     # remove only the shortcuts Crabture added
crabture --install-integration    # add the right-click entries to Nautilus (--remove-integration undoes it)
crabture --backend-info           # which capture backend would be used
```

The starter capture shortcuts are `Ctrl+Shift+Print` (region), `Ctrl+Print` (full screen) and `Ctrl+Alt+Print` (window); they are only given to the desktop when you press "Apply to desktop", because surprise keyboard shortcuts are rude. The first Wayland capture may show a GNOME permission prompt. That's GNOME checking you really meant it. A save-only capture that also copies to the clipboard keeps a tiny background process alive until something else is copied (otherwise the clipboard would vanish with it). Scrolling capture on Wayland **cannot auto-scroll** (the compositor won't let apps drive your mouse, which is fair): scroll yourself and press `Space` (or the button) to grab each frame.

Or run straight from the source tree with `python3 main.py`, like a purist.

## ⌨️ Shortcuts
*All of these are remappable in Configure, so feel free to ruin them.*

| Action | Shortcut |
| :--- | :--- |
| **Open Image** | `Ctrl + O` |
| **Save Image (overwrites the original — read that again)** | `Ctrl + S` |
| **Save As** | `Ctrl + Shift + S` |
| **Copy Canvas to Clipboard** | `Ctrl + Shift + C` |
| **Power Rename** (selected gallery items) | `Ctrl + R` |
| **Undo / Redo** | `Ctrl + Z` / `Ctrl + Y` |
| **Zoom In / Out** | `Ctrl + =` / `Ctrl + -` (or `Ctrl` + mouse wheel) |
| **Fit to Window** | `Ctrl + 0` |
| **Delete Selected Item** | `Delete` |
| **Image Effects** | `Ctrl + E` |
| **Run Sensitive-Data Scan** | `Ctrl + Shift + F` |
| **Preferences / Configure** | `Ctrl + ,` |
| **Keyboard shortcut reference** | `F1` |
| **Toggle side panel** | `F9` |
| **Quit** | `Ctrl + Q` |

### Tool shortcuts (single keys, canvas focused)
*Memorise these and you'll never touch the toolbar again. You'll still touch the toolbar.*

| Key | Tool | Key | Tool |
| :--- | :--- | :--- | :--- |
| `S` | Select | `H` | Highlight |
| `A` | Arrow | `P` | Pixelate |
| `R` | Rectangle | `B` | Blur |
| `T` | Text | `M` | Magnifier |
| `C` | Crop | `E` | Ellipse |
| `L` | Line | `D` | Freehand pen |
| `N` | Numbered step | `O` | Spotlight |
| `Y` | Sticker | `I` | Eyedropper |
| `K` | Speech balloon | `Space` | Hand (hold to pan) |

Hold `Space` to pan around a zoomed-in image with the mouse, whatever tool is active. Let go and you're back where you were. You can also pick the Hand tool from the tool rail to stay in pan mode.

While cropping, `Enter` confirms and `Esc` cancels. While using the eyedropper, `Esc` takes you back to your previous tool, and `Enter` picks the colour under the pointer.

## 🗺 Project layout
*A tour, for the three people who'll ever read this.*

```
Crabture/
├── main.py                 # thin launcher: `python3 main.py`
├── LICENSE                 # GPL-3.0-or-later
├── README.md
├── requirements.txt
├── assets/                 # logo.png (the app icon), logo.svg
├── scripts/
│   ├── builder.sh          # install / reinstall / uninstall / build / --deb (Linux)
│   ├── build_windows.ps1   # PyInstaller bundle + optional Inno Setup installer (Windows)
│   └── make_icon.py        # assets/logo.png -> assets/logo.ico
├── packaging/              # the .deb's control bits, man page, desktop file; nautilus/ has the right-click scripts; windows/ has the Inno Setup script
└── crabture/               # the actual application (a proper Python package, finally)
    ├── app.py              # command-line parsing and startup
    ├── version.py          # the one place the version number lives
    ├── resources.py        # finds assets/ whether run from source, .deb or PyInstaller
    ├── ui/                 # main window, panels, dialogs, tool rail, welcome page, theme, icons
    ├── editor/             # canvas, annotation items, undo/layer models, eyedropper loupe
    ├── osinfo.py           # the few OS differences: data dirs, process kill, reveal-in-file-manager
    ├── ipc.py              # hands `--capture` to an already running Crabture
    ├── integration.py      # installs/removes the right-click (Nautilus) scripts for the current user
    ├── capture/            # screen capture (X11, Wayland, Windows): service, overlays, controller, saving/config, desktop hotkeys (GNOME/Xfce/Windows)
    ├── effects/            # border/shadow/torn-edge/watermark effects and their dialog
    ├── redact/             # sensitive-data finder, saved profile, dialogs
    ├── rename/             # Power Rename dialog and its undo journal
    └── settings/           # Configure window (incl. the Capture tab), remappable shortcuts, per-tool defaults
```

Run it from source with `python3 main.py`. Poke at the guts with e.g. `python3 -m crabture.ui.icons /tmp/icons` (writes an icon contact sheet).

## 🛠 Prerequisites

- Python 3.10+ on Linux or Windows 10/11
- PyQt6 (`pip install -r requirements.txt`)
- Optional, but you'll want them: Tesseract OCR (sensitive-data scan); on Linux also `xdotool` (X11 window capture and auto-scroll) and `maim`/`scrot`/`gnome-screenshot`/`grim` (extra capture backends)

## 🤷 Honest disclaimers

- Most of this was tested headless, with mocks, in a sandbox. Your actual desktop remains the final boss. The Windows port in particular was written and unit-tested on Linux (Win32 calls mocked): a real Windows machine hasn't had its say yet. If something looks off on a real screen, tell us. We'll act surprised.
- Real OCR quality depends on how `tesseract` feels about your screenshot that day.

## 📜 Licence

Crabture is free software, released under the **GNU General Public License, version 3 or (at your option) any later version** (`GPL-3.0-or-later`). The full text is in [`LICENSE`](LICENSE).

In plain language, for people who skipped the legalese: use it, change it, share it, even sell it. If you distribute a modified version, you must keep it under the same licence and share the source. There is no warranty, so if Crabture blurs the wrong thing, that one's on you.

Why GPL and not something more relaxed? Because Crabture is built on **PyQt6**, which is itself GPLv3 (or commercial), and the standalone binary from `./scripts/builder.sh --build` bundles it. Picking the same licence keeps everything honest and legal. Wanting something more permissive would mean buying a commercial PyQt licence or moving to PySide6 (LGPL). Not today.

Copyright © 2026 YawnByte.

---

Developed as part of the YawnByte project suite. Now with 100% more crab.

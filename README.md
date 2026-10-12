# 🦀 Crabture — Yet Another Screenshot Tool (But Make It Crab)

**Crabture** is a ShareX-style screenshot tool for Debian, Kali and other Linux desktops. Yes, there are already a hundred of these. No, none of them were *this* crab-themed.

It captures your screen (X11 *and* Wayland, because apparently we can't have nice things in just one), lets you scribble on it, makes it look professional with effects, and then manages the pile of PNGs you will inevitably accumulate. Built with **PyQt6**, held together by optimism and an unreasonable number of throwaway test scripts that live outside the repo (so, no, there is no test suite. Yet.).

![Crabture Logo](assets/logo.png)

## ✨ Features (a.k.a. "Things We Did Instead of Sleeping")

### 📷 Screen capture
*Because Print Screen alone was apparently too easy.*

- **Region** (a frozen-screen overlay where you drag a rectangle, like it's 2009), **fullscreen**, **active window**, **monitor under the cursor**, and **scrolling capture** (you scroll, Crabture stitches the frames, everyone pretends it was effortless).
- Works on **X11** and **Wayland**. On Wayland it talks to the xdg-desktop-portal like a well-behaved citizen; on X11 it can also use `maim`, `scrot` or ImageMagick `import` if you have them lying around. Window capture on Wayland opens the system picker, because the compositor said "no" to everything else.
- Options for a **delay**, **hiding Crabture while capturing** (so you don't screenshot Crabture screenshotting — truly a groundbreaking feature), and **copy to clipboard**. Captures land in `~/Downloads/Crabshots` and open in the editor automatically.
- From the command line: `crabture --capture region|fullscreen|window|monitor|scroll [--delay S]`.
- `crabture --register-hotkeys` (or the Capture menu) adds GNOME shortcuts. It **never overwrites** one you already use. We're not monsters.

### 🖍 Annotation tools
*Seventeen of them. You'll use three.*

`Select`, `Arrow`, `Rectangle`, `Highlight`, `Pixelate`, `Blur`, `Text`, `Crop`, `Magnifier`, `Speech Balloon`, `Ellipse`, `Line`, freehand `Pen`, numbered `Step` markers, `Spotlight` (dims everything except the bit you want people to look at, i.e. the thing they'd have missed anyway), emoji `Sticker` (click the tool twice to pick one — yes, this is a professional tool) and an `Eyedropper`.

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
- Right-click for Open, Power Rename, Open in File Manager, or Copy Path.

### ⚡ Power Rename (`Ctrl+R` on selected gallery items)
*Because `Screenshot from 2026-10-12 19-19-10 (3).png` is not a name, it's a cry for help.*

- **Pipeline:** Find & Replace (plain or regex with capture groups, whole-word matching) → name template/numbering → insert/remove → case conversion → cleanup.
- **Template tokens:** `{name}` `{ext}` `{n:03}` `{date}` `{mtime:%Y-%m-%d}` `{parent}` `{w}` `{h}` `{size}` `{rand:6}` `{original}`. Numbering can follow name (natural sort), date, size or dimensions.
- Presets (built-in and your own), a live preview with conflict detection, **safe swaps** (rename a→b and b→a without anything exploding), and **Undo Last Rename** (`Edit ▸ Undo Last Rename`) for the moment you regret everything.

### 🐧 Linux integration
`./scripts/builder.sh --install` adds an application-menu entry (so Crabture shows up in "Open With" for common image types) and two Nautilus scripts (right-click ▸ Scripts):

- **Open Directory with Crabture**
- **Edit with Crabture** — opens the selected image in the editor with its folder in the gallery. Two whole right-click entries. We're spoiling you.

## 🚀 Installation
*Steps: three. Difficulty: you'll survive.*

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
crabture path/to/image.png        # open an image in the editor
crabture --directory ~/Pictures   # show a folder in the gallery (-d)
crabture --capture region         # take a screenshot straight away
crabture --capture window --delay 3
crabture --register-hotkeys       # add GNOME shortcuts (Ctrl+Print, Ctrl+Shift+Print, Ctrl+Alt+Print)
crabture --unregister-hotkeys     # remove only the shortcuts Crabture added
crabture --backend-info           # which capture backend would be used
```

The first Wayland capture may show a GNOME permission prompt. That's GNOME checking you really meant it. Scrolling capture on Wayland **cannot auto-scroll** (the compositor won't let apps drive your mouse, which is fair): scroll yourself and press `Space` (or the button) to grab each frame.

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
| `K` | Speech balloon | | |

While cropping, `Enter` confirms and `Esc` cancels. While using the eyedropper, `Esc` takes you back to your previous tool, and `Enter`/`Space` picks the colour under the pointer.

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
│   └── builder.sh          # install / reinstall / uninstall / build / --deb
├── packaging/              # everything the .deb needs (control bits, man page, desktop file)
└── crabture/               # the actual application (a proper Python package, finally)
    ├── app.py              # command-line parsing and startup
    ├── version.py          # the one place the version number lives
    ├── resources.py        # finds assets/ whether run from source, .deb or PyInstaller
    ├── ui/                 # main window, panels, dialogs, tool rail, welcome page, theme, icons
    ├── editor/             # canvas, annotation items, undo/layer models, eyedropper loupe
    ├── capture/            # screen capture (X11 + Wayland), overlays, GNOME hotkeys
    ├── effects/            # border/shadow/torn-edge/watermark effects and their dialog
    ├── redact/             # sensitive-data finder, saved profile, dialogs
    ├── rename/             # Power Rename dialog and its undo journal
    └── settings/           # Configure window, remappable shortcuts, per-tool defaults
```

Run it from source with `python3 main.py`. Poke at the guts with e.g. `python3 -m crabture.ui.icons /tmp/icons` (writes an icon contact sheet).

## 🛠 Prerequisites

- Python 3.10+
- PyQt6 (`pip install -r requirements.txt`)
- Optional, but you'll want them: `tesseract-ocr` (sensitive-data scan), `xdotool` (X11 window capture and auto-scroll), `maim`/`scrot`/`gnome-screenshot`/`grim` (extra capture backends)

## 🤷 Honest disclaimers

- Most of this was tested headless, with mocks, in a sandbox. Your actual desktop remains the final boss. If something looks off on a real screen, tell us. We'll act surprised.
- Real OCR quality depends on how `tesseract` feels about your screenshot that day.

## 📜 Licence

Crabture is free software, released under the **GNU General Public License, version 3 or (at your option) any later version** (`GPL-3.0-or-later`). The full text is in [`LICENSE`](LICENSE).

In plain language, for people who skipped the legalese: use it, change it, share it, even sell it. If you distribute a modified version, you must keep it under the same licence and share the source. There is no warranty, so if Crabture blurs the wrong thing, that one's on you.

Why GPL and not something more relaxed? Because Crabture is built on **PyQt6**, which is itself GPLv3 (or commercial), and the standalone binary from `./scripts/builder.sh --build` bundles it. Picking the same licence keeps everything honest and legal. Wanting something more permissive would mean buying a commercial PyQt licence or moving to PySide6 (LGPL). Not today.

Copyright © 2026 YawnByte.

---

Developed as part of the YawnByte project suite. Now with 100% more crab.

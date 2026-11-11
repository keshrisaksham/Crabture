# 🦀 Crabture — Yet Another Screenshot Tool (But WAY BETTER!)

**Crabture** is a ShareX-style screenshot tool for Debian, Kali and other Linux desktops (and Windows, if you're brave). Yes, there are a hundred of these already. None of them were *this* crab-themed.

It **captures** your screen (X11 *and* Wayland, because we can't have nice things in just one), lets you **annotate** it, makes it look professional with **effects**, **redacts** the API key you just leaked, and **renames** the pile of `Screenshot from 2026-10-12 19-19-10 (3).png` files you'll inevitably collect. Built with **PyQt6** and held together by optimism. There is no test suite. Yet.

![Crabture Logo](assets/logo.png)

## 🚀 Quick start

```bash
pip install -r requirements.txt     # PyQt6. That's the whole dependency list.
python3 main.py                     # run from source, like a purist
./scripts/builder.sh --install      # or: menu entry + `crabture` command + right-click scripts
```

`--install` points at your clone, so **don't move or delete it** or Crabture vanishes in protest. Put `~/.local/bin` on your `PATH`.

## 📷 Capture

Five modes: `region` (frozen-screen selector with a pixel loupe), `fullscreen`, `window`, `monitor` and `scroll` (you scroll, Crabture stitches, everyone pretends it was effortless).

```bash
crabture --capture region                          # the classic
crabture --capture window --delay 3                # 3 s to arrange the scene (or escape your terminal)
crabture --capture fullscreen --after save         # no window at all, just a file + a notification
crabture --capture region --clipboard              # also copy it to the clipboard
```

### Script it, it's fine

`-o`/`-p` turn the command into a self-contained one-shot: it waits, prints the saved path on stdout, and tells you how it went with its exit code.

```bash
crabture --capture fullscreen -o ~/shot.png                  # exactly this file (.png .jpg .webp .bmp)
crabture --capture region -o ~/Work/                         # into a folder, default naming
crabture --capture region --folder ~/Work -n "bug_{mode}_%H-%M"   # name just this one (strftime codes + {mode})
crabture --capture fullscreen -o ~/a.png --copy-to /mnt/share --copy-to ~/Backup   # one shot, three places
path=$(crabture --capture monitor -p) && echo "saved: $path"      # stdout is ONLY the path(s)
```

| Exit code | Meaning |
| :--- | :--- |
| `0` | saved (the path is on stdout) |
| `1` | failed (reason on stderr) |
| `2` | cancelled, or you typed something wrong |
| `130` / `143` | Ctrl+C / `kill`, ended cleanly |

- Safe to run **in parallel**: jobs queue for the desktop's screenshot service and never overwrite each other's file.
- Never handed to a running Crabture, no notification, `--after` defaults to `save`.
- `--copy-to` folders are *also* configurable in Configure ▸ Capture (**Also save a copy to**). A folder you can't write to is skipped and reported; the shot is never lost.
- `region`, `scroll` and window picking need a human with a mouse; `fullscreen` and `monitor` run happily unattended. Needs a graphical session.
- `--clipboard` keeps the process alive until something else is copied (otherwise the clipboard dies with it), so add `&` in scripts. Want a hard limit? `timeout 30 crabture ...`.

### Where it works, how it saves

- **X11:** Qt, then `maim` / `scrot` / ImageMagick `import`. **Wayland:** the xdg-desktop-portal (via a tiny invisible helper window so GNOME stays silent), then `gnome-screenshot`, then `grim`. Prefer your desktop's own tool? Configure ▸ Capture ▸ engine. A failing tool falls through to the next.
- On Wayland, *window* mode outlines the window under your pointer by looking for borders in the frozen picture (`↑`/`↓` or scroll to resize the guess, `Space` to draw a region instead). Scrolling capture can't auto-scroll there; scroll yourself and press `Space` per frame.
- Folder (default `~/Downloads/Crabshots`), name pattern (`Crabture_%Y-%m-%d_%H-%M-%S`) and format (PNG / JPEG / WebP) are all in Configure ▸ Capture. Files appear atomically, so nothing ever sees half a screenshot.

### Global shortcuts

Make as many as you like: each one takes one kind of screenshot with its own delay, folder and "then" (save or open the editor). Starters: `Ctrl+Shift+Print` region, `Ctrl+Print` full screen, `Ctrl+Alt+Print` window.

```bash
crabture --register-hotkeys        # hand them to GNOME / Xfce (never overwrites a key you already use)
crabture --unregister-hotkeys      # removes only the ones Crabture added
```

Shortcuts reach a running Crabture instantly; if none is running, a save-only shortcut runs with no window at all. Other desktops get the exact commands to bind by hand. Shortcuts are **never** registered behind your back. Surprise keybindings are rude.

## 🖍 Edit

Seventeen annotation tools, of which you'll use three: `Select` `Arrow` `Rectangle` `Highlight` `Pixelate` `Blur` `Text` `Crop` `Magnifier` `Speech Balloon` `Ellipse` `Line` `Pen` `Step` (numbered markers) `Spotlight` `Sticker` (yes, emoji) `Eyedropper` (with a pixel-exact loupe) `Hand`.

- Every annotation is **its own layer** (reorder, hide, delete). Photoshop called; it's flattered.
- Pixelate and Blur are **live** effects with an intensity slider. Full undo/redo. `Shift` constrains angles and squares.
- 8 preset colours, a custom picker, opacity. Zoom, fit, pan with `Space`. Drag and drop images or folders onto the window.
- **Effects** (`Ctrl+E`): border, shadow, torn edge, watermark, padded gradient frame. Live preview, presets, one undo.

## 🛡 Redact

Needs `tesseract-ocr` (yes, you install it yourself). **Configure ▸ Sensitive Data…** picks detectors (emails, IPs, API keys, tokens, JWTs, passwords, card numbers…) plus your own regexes. **Run it** with `Ctrl+Shift+F`. Nothing is scanned or changed until *you* click Run, and by default nothing is blurred until you've reviewed the findings. Every redaction is an ordinary undoable layer, so a wrongly blurred cat is one `Ctrl+Z` from freedom.

## 🗂 Gallery & ⚡ Power Rename

The gallery watches a folder in four views (Grid, List, Details, Compact) with natural sorting (`Test_2` before `Test_10`, mathematicians rejoice). Select several files to open them as one session.

**Power Rename** (`Ctrl+R` on selected items): find & replace (plain or regex), patterns like `{name} {ext} {n:03} {date} {mtime:%Y-%m-%d} {parent} {w} {h} {size} {rand:6} {original}`, add/remove text, letter case, tidy-up. Live preview with conflict detection, **safe swaps** (a→b and b→a), and **Edit ▸ Undo Last Rename** for when you regret everything.

```bash
crabture path/to/image.png          # open an image (the gallery shows its folder)
crabture a.png b.png c.png          # several images, one session
crabture --directory ~/Pictures     # show a folder in the gallery (-d)
```

## ⚙ Configure (`Ctrl+,`)

Remap **every** tool key and menu shortcut (conflict checks, import/export), per-tool defaults (stroke widths, blur strength, …), System/Light/Dark theme, and every capture setting above. Always starts maximized. That's not a bug, it's a lifestyle.

## 🐧🪟 Integration, packages, other platforms

```bash
./scripts/builder.sh --install | --uninstall | --reinstall   # from source (menu entry + launcher + Nautilus scripts)
./scripts/builder.sh --build                                 # standalone binary via PyInstaller -> dist/crabture
./scripts/builder.sh --deb                                   # -> dist/crabture_<version>_all.deb
sudo apt install ./dist/crabture_*_all.deb                   # install it (don't ALSO run --install, your PATH will pick wrong)
crabture --install-integration | --remove-integration        # right-click ▸ Scripts ▸ "Edit with Crabture" / "Open Directory with Crabture"
crabture --backend-info                                      # which capture backend would be used
crabture --help                                              # everything above, but official
```

Everything you open (right-click, *Open With*, command line) lands in **one** window, even when the file manager launches Crabture once per file.

**Windows 10/11** (Python 3.10+): `py -3 -m pip install -r requirements.txt && py -3 main.py`. Build the installer with `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Installer` (needs Inno Setup 6). Capture uses Qt plus Crabture's own selector; *Apply to desktop* starts Crabture in the tray at sign-in (`crabture --background`) and registers hotkeys itself. OCR: `winget install UB-Mannheim.TesseractOCR`. Settings live in the registry (`HKCU\Software\YawnByte\Crabture`).

## ⌨️ Shortcuts (all remappable, so feel free to ruin them)

| Action | Key | Action | Key |
| :--- | :--- | :--- | :--- |
| Open / Save / Save As | `Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S` | Undo / Redo | `Ctrl+Z` / `Ctrl+Y` |
| Copy canvas | `Ctrl+Shift+C` | Zoom in / out / fit | `Ctrl+=` / `Ctrl+-` / `Ctrl+0` |
| Effects | `Ctrl+E` | Sensitive-data scan | `Ctrl+Shift+F` |
| Power Rename | `Ctrl+R` | Preferences | `Ctrl+,` |
| Shortcut reference | `F1` | Toggle side panel | `F9` |

`Ctrl+S` **overwrites the original.** Read that again.

Tools: `S` select · `A` arrow · `R` rectangle · `H` highlight · `P` pixelate · `B` blur · `T` text · `C` crop · `M` magnifier · `K` balloon · `E` ellipse · `L` line · `D` pen · `N` step · `O` spotlight · `Y` sticker · `I` eyedropper · hold `Space` to pan. While cropping, `Enter` confirms and `Esc` cancels.

## 🗺 Layout

```
main.py                 thin launcher
crabture/app.py         command line + startup (the CLI lives here)
crabture/capture/       grab (X11/Wayland/Windows), overlays, controller, saving/config, desktop hotkeys
crabture/editor/        canvas, annotation items, undo + layers
crabture/ui/            main window, panels, theme, icons
crabture/effects|redact|rename|settings/   what the names say
crabture/ipc.py         single-instance command channel + capture locks
scripts/ packaging/     builder, .deb, Windows installer, man page, Nautilus scripts
```

Prerequisites: Python 3.10+, PyQt6. Optional but wanted: `tesseract-ocr` (redaction); on Linux `xdotool` (X11 window capture, auto-scroll) and `maim`/`scrot`/`gnome-screenshot`/`grim` (extra backends).

## 🤷 Honest disclaimers

Mostly tested headless with mocks, so your real desktop remains the final boss. The Windows port was written and unit-tested on Linux (Win32 calls mocked); a real Windows machine hasn't had its say. Region and window picking need a real screen and a real human. OCR quality depends on how `tesseract` feels about your screenshot that day.

## 📜 Licence

**GPL-3.0-or-later**, full text in [`LICENSE`](LICENSE). Use it, change it, share it; if you distribute a modified version, share the source too. No warranty: if Crabture blurs the wrong thing, that one's on you. (It's GPL because PyQt6 is GPLv3; something more permissive means a commercial PyQt licence or PySide6.)

Copyright © 2026 YawnByte. Developed as part of the YawnByte project suite. Now with 100% more crab.

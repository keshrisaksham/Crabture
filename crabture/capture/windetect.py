"""
capture/windetect.py — find the window under a point in a screenshot.

Wayland compositors do not tell an application where other windows are, so for "window" capture
Crabture looks at the frozen picture of the desktop instead: a window is a rectangle whose four
sides are long, straight, high-contrast lines.  From a point inside it, the detector walks out in
the four directions and picks, on each side, the nearest line that is almost as long as the longest
one found (ignoring short lines made by text and icons).

It is a heuristic and can be wrong (two dark windows side by side, a window with no visible border);
the picker lets the user switch to dragging a region by hand.

Pure Python on a QImage (no numpy): the picture is reduced to grey, 1/``STEP`` size, once; every
lookup afterwards is a few thousand byte comparisons.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QImage

STEP = 3                    # analysis scale: 1 analysed pixel = STEP real pixels
EDGE = 9                    # grey-level jump that counts as an edge (0..255)
SOLID = 0.8                # share of a line's pixels (full resolution) that must be edge pixels
LAG = 3                    # edges are measured across this many pixels: soft shadows still count
GAP = 2                     # a line may have this many missing pixels and still be one line
MIN_RUN_PX = 140            # a side must be at least this long (real pixels) to count as a window side
MAX_PER_SIDE = 6            # how many of the nearest border lines per side are tried
BIG_WINDOW = 0.7            # a rectangle this share of the screen counts as a (nearly) full-screen window
ALT_RANGE = 0.3             # rectangles scoring this close to the best are offered as alternatives
MAX_CANDIDATES = 6
TIE = 0.10                   # scores this close to the best count as equally good
DESKTOP_LINE = 0.95         # fit score of a side that is a screen-wide line (top bar, dock)
EDGE_OF_SCREEN = 0.6        # fit score of a side that is simply the screen edge
SNAP_PX = 10                # a window edge this close to the screen edge is the screen edge
MIN_SIZE_PX = 60            # a window smaller than this is not a window


class EdgeMap:
    """Edge lines of one picture; ``window_at(x, y)`` gives the window rectangle (real pixels)."""

    def __init__(self, image: QImage) -> None:
        self._full = image.convertToFormat(QImage.Format.Format_Grayscale8)
        self.width, self.height = self._full.width(), self._full.height()
        self._rows_full: List[bytes] = self._rows(self._full)
        small = self._full.scaled(max(1, self.width // STEP), max(1, self.height // STEP),
                                  Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._w, self._h = small.width(), small.height()
        rows = self._rows(small)
        w, h = self._w, self._h
        # vcols[x][y] = 1: a vertical edge between x-1 and x at row y.   hrows[y][x] = 1: horizontal edge between y-1 and y.
        self._vcols: List[bytearray] = [bytearray(h) for _ in range(w)]
        self._hrows: List[bytearray] = [bytearray(w) for _ in range(h)]
        for y in range(h):
            row = rows[y]
            prev_row = rows[y - 1] if y else None
            hrow = self._hrows[y]
            for x in range(1, w):
                if abs(row[x] - row[x - 1]) > EDGE:
                    self._vcols[x][y] = 1
            if prev_row is not None:
                for x in range(w):
                    if abs(row[x] - prev_row[x]) > EDGE:
                        hrow[x] = 1

    @staticmethod
    def _rows(img: QImage) -> List[bytes]:
        stride = img.bytesPerLine()
        data = bytes(img.constBits().asarray(stride * img.height()))
        return [data[y * stride: y * stride + img.width()] for y in range(img.height())]

    # -- lookups -------------------------------------------------------------

    @staticmethod
    def _run(line: bytearray, at: int) -> Tuple[int, int]:
        """Extent (first, last) of the edge line through index *at*, bridging gaps of up to GAP."""
        n = len(line)
        lo = hi = at
        miss = 0
        i = at - 1
        while i >= 0:
            if line[i]:
                lo, miss = i, 0
            else:
                miss += 1
                if miss > GAP:
                    break
            i -= 1
        miss = 0
        i = at + 1
        while i < n:
            if line[i]:
                hi, miss = i, 0
            else:
                miss += 1
                if miss > GAP:
                    break
            i += 1
        return lo, hi

    def _lines(self, lines, start: int, step: int, along: int, limit: int, bound: int, vertical: bool):
        """Every long, solid edge line met walking from *start* by *step*, nearest first.

        Candidates come from the coarse map, then each is checked and positioned on the full-resolution
        picture.  Returns ``(position, first, last)`` in picture pixels.
        """
        min_run = max(6, MIN_RUN_PX // STEP)
        found = []
        last_pos = None
        i = start
        while 0 <= i < limit:
            line = lines[i]
            hit = None
            for d in range(-GAP, GAP + 1):         # the line may pass a pixel or two off the pointer
                a = along + d
                if 0 <= a < bound and line[a]:
                    hit = a
                    break
            if hit is not None:
                lo, hi = self._run(line, hit)
                if hi - lo + 1 >= min_run:
                    real = self._verify(i, lo, hi, vertical)
                    # neighbouring coarse lines are usually one border: keep the first
                    if real is not None and (last_pos is None or abs(real[0] - last_pos) > 2 * STEP):
                        found.append(real)
                        last_pos = real[0]
            i += step
        return found

    def _verify(self, idx: int, lo: int, hi: int, vertical: bool):
        """Best full-resolution line near coarse line *idx* spanning coarse *lo*..*hi*; None if it is not solid."""
        first, last = lo * STEP, (hi + 1) * STEP
        if vertical:
            last = min(last, self.height)
            samples = range(first, last, max(1, (last - first) // 100))
            best, best_pos = -1, 0
            for cx in range(max(LAG, idx * STEP - STEP - 1), min(self.width, idx * STEP + STEP + 2)):
                score = sum(abs(self._rows_full[y][cx] - self._rows_full[y][cx - LAG]) > EDGE for y in samples)
                if score > best:
                    best, best_pos = score, cx
        else:
            last = min(last, self.width)
            samples = range(first, last, max(1, (last - first) // 100))
            best, best_pos = -1, 0
            for cy in range(max(LAG, idx * STEP - STEP - 1), min(self.height, idx * STEP + STEP + 2)):
                upper, lower = self._rows_full[cy - LAG], self._rows_full[cy]
                score = sum(abs(lower[x] - upper[x]) > EDGE for x in samples)
                if score > best:
                    best, best_pos = score, cy
        if not len(samples) or best < SOLID * len(samples):
            return None
        return best_pos, first, last - 1

    def _panel_bottom(self) -> int:
        """Bottom of a uniform bar along the top of the picture (0 if there is none)."""
        top = self._rows_full[0]
        cols = range(0, self.width, 8)
        limit = int(0.08 * self.height)
        for yy in range(6, limit):
            row = self._rows_full[yy]
            differing = sum(abs(row[x] - top[x]) > EDGE for x in cols)
            if differing >= 0.5 * len(cols):
                return yy
        return 0

    @staticmethod
    def _fit(line, a: int, b: int) -> float:
        """How well a border line (pos, first, last) fits a side that should span [a, b]: 0..1.

        1.0 = the line covers the side exactly; a line that is much longer (the desktop's top bar over a
        half-width window) or much shorter (a text rule) scores low.
        """
        _p, lo, hi = line
        inter = max(0, min(hi, b) - max(lo, a))
        span, length = max(1, b - a), max(1, hi - lo)
        return min(1.0, (inter / span) * (inter / length) ** 0.5)

    def window_at(self, x: int, y: int) -> Optional[QRect]:
        """The most likely window rectangle (picture pixels) around the point, or None."""
        rects, best = self.candidates_at(x, y)
        return rects[best] if rects else None

    def candidates_at(self, x: int, y: int) -> Tuple[List[QRect], int]:
        """Plausible rectangles around the point, smallest to largest, and the index of the best guess.

        Every combination of the nearest few border lines on the four sides is a candidate rectangle, scored by
        how well its sides fit it.  The best guess is the highest score (near-ties: the larger one); the others
        let the user step to a bigger or smaller one when the guess is wrong (a window and the panel inside it).
        """
        sx = min(max(x // STEP, 0), self._w - 1)
        sy = min(max(y // STEP, 0), self._h - 1)
        lefts = self._lines(self._vcols, sx, -1, sy, self._w, self._h, True)[:MAX_PER_SIDE]
        rights = self._lines(self._vcols, sx + 1, +1, sy, self._w, self._h, True)[:MAX_PER_SIDE]
        tops = self._lines(self._hrows, sy, -1, sx, self._h, self._w, False)[:MAX_PER_SIDE]
        bottoms = self._lines(self._hrows, sy + 1, +1, sx, self._h, self._w, False)[:MAX_PER_SIDE]
        # The screen edge is always a possible side (a maximised window has no line there).
        lefts.append((0, None, None))
        rights.append((self.width, None, None))
        tops.append((0, None, None))
        bottoms.append((self.height, None, None))

        scored = []
        for l in lefts:
            for r in rights:
                if r[0] - l[0] < MIN_SIZE_PX:
                    continue
                for t in tops:
                    for b in bottoms:
                        if b[0] - t[0] < MIN_SIZE_PX:
                            continue
                        score = 0.0
                        for line, lo, hi, full in ((l, t[0], b[0], self.height), (r, t[0], b[0], self.height),
                                                   (t, l[0], r[0], self.width), (b, l[0], r[0], self.width)):
                            if line[1] is None:
                                score += EDGE_OF_SCREEN
                            elif line[2] - line[1] >= 0.98 * full:
                                score += DESKTOP_LINE        # spans the whole screen: a panel or dock, i.e. the desktop
                            else:
                                score += self._fit(line, lo, hi)
                        scored.append((score, (r[0] - l[0]) * (b[0] - t[0]), (l[0], t[0], r[0], b[0])))
        if not scored:
            return [], 0
        top_score = max(sc for sc, _a, _r in scored)
        best_raw = max((a, rect) for sc, a, rect in scored if sc >= top_score - TIE)[1]
        pool = {best_raw}
        pool.update(rect for sc, _a, rect in sorted(scored, key=lambda t: -t[0])[:60] if sc >= top_score - ALT_RANGE)

        rects: List[QRect] = []
        best_rect: Optional[QRect] = None
        for raw in sorted(pool, key=lambda r: (r[2] - r[0]) * (r[3] - r[1])):
            rect = self._tidy(raw)
            if rect is None:
                continue
            if raw == best_raw:
                best_rect = rect
            if not any(self._same(rect, other) for other in rects):
                rects.append(rect)
        if not rects:
            return [], 0
        if best_rect is None:
            best_rect = rects[-1]
        best = min(range(len(rects)), key=lambda i: abs(rects[i].x() - best_rect.x()) + abs(rects[i].y() - best_rect.y())
                   + abs(rects[i].width() - best_rect.width()) + abs(rects[i].height() - best_rect.height()))
        if len(rects) > MAX_CANDIDATES:              # keep the ones around the best guess
            lo = max(0, min(best - MAX_CANDIDATES // 2, len(rects) - MAX_CANDIDATES))
            rects, best = rects[lo:lo + MAX_CANDIDATES], best - lo
        return rects, best

    @staticmethod
    def _same(a: QRect, b: QRect, tol: int = 8) -> bool:
        return (abs(a.x() - b.x()) <= tol and abs(a.y() - b.y()) <= tol
                and abs(a.width() - b.width()) <= tol and abs(a.height() - b.height()) <= tol)

    def _tidy(self, raw) -> Optional[QRect]:
        x0, y0, x1, y1 = raw
        # A (nearly) full-screen window sits under the desktop's top bar: that bar is a uniform strip along
        # the top whose bottom edge may be too faint (a pale tab next to it) for the line search.
        if (x1 - x0) * (y1 - y0) >= BIG_WINDOW * self.width * self.height and y0 <= 0.12 * self.height:
            panel = self._panel_bottom()
            if panel:
                y0 = panel
        # A window that ends a few pixels short of the screen edge is a window touching it (borders, rounding).
        x0, y0 = (0 if x0 <= SNAP_PX else x0), (0 if y0 <= SNAP_PX else y0)
        x1 = self.width if x1 >= self.width - SNAP_PX else x1
        y1 = self.height if y1 >= self.height - SNAP_PX else y1
        if x1 - x0 < MIN_SIZE_PX or y1 - y0 < MIN_SIZE_PX:
            return None
        return QRect(x0, y0, x1 - x0, y1 - y0)

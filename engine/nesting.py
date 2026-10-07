"""
2D rectangle nesting (MaxRects, Best-Short-Side-Fit).

Plain-language summary
----------------------
Each surplus piece (a sheet or an offcut) is a "bin". We place identical
rectangular parts into it one at a time. The algorithm keeps a list of the
largest empty rectangles still available ("free rectangles"). For every new
part it picks the free rectangle where the part fits most snugly (smallest
leftover on the short side), places it there, and recomputes the free space.

Kerf and edge margin
--------------------
- edge_margin_mm: a strip along every edge that we never cut into
  (rough or damaged edges). The usable area shrinks by 2 x margin each way.
- kerf_mm: the width of material the cutting tool removes. Every part is
  grown by one kerf, and the usable area is grown by one kerf too, so the
  outermost parts don't waste a kerf against the edge.

This module has no database or web code, so it is easy to unit-test.
"""
from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-9


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def top(self) -> float:
        return self.y + self.h

    def contains(self, other: "Rect") -> bool:
        return (
            self.x <= other.x + EPS
            and self.y <= other.y + EPS
            and self.right + EPS >= other.right
            and self.top + EPS >= other.top
        )

    def intersects(self, other: "Rect") -> bool:
        return not (
            other.x >= self.right - EPS
            or other.right <= self.x + EPS
            or other.y >= self.top - EPS
            or other.top <= self.y + EPS
        )


@dataclass(frozen=True)
class Placement:
    """A part placed on a piece, in real piece coordinates (mm, part size without kerf)."""

    x: float
    y: float
    length: float
    width: float
    rotated: bool


class MaxRectsBin:
    def __init__(self, width: float, height: float, allow_rotation: bool = True):
        self.width = width
        self.height = height
        self.allow_rotation = allow_rotation
        self.free: list[Rect] = [Rect(0.0, 0.0, width, height)]
        self.used: list[tuple[Rect, bool]] = []

    def _find_position(self, w: float, h: float):
        best = None  # (short_fit, long_fit, Rect, rotated)
        options = [(w, h, False)]
        if self.allow_rotation and abs(w - h) > EPS:
            options.append((h, w, True))
        for fr in self.free:
            for ow, oh, rot in options:
                if ow <= fr.w + EPS and oh <= fr.h + EPS:
                    lw, lh = fr.w - ow, fr.h - oh
                    score = (min(lw, lh), max(lw, lh))
                    if best is None or score < best[:2]:
                        best = (score[0], score[1], Rect(fr.x, fr.y, ow, oh), rot)
        return best

    def block(self, area: Rect) -> None:
        """Mark an area as unusable (a defect) before packing."""
        self._split_free(area)
        self._prune_free()

    def insert(self, w: float, h: float):
        found = self._find_position(w, h)
        if found is None:
            return None
        _, _, node, rotated = found
        self._split_free(node)
        self._prune_free()
        self.used.append((node, rotated))
        return node, rotated

    def _split_free(self, used: Rect) -> None:
        new_free: list[Rect] = []
        for fr in self.free:
            if not fr.intersects(used):
                new_free.append(fr)
                continue
            if used.x > fr.x + EPS:  # left slice
                new_free.append(Rect(fr.x, fr.y, used.x - fr.x, fr.h))
            if used.right < fr.right - EPS:  # right slice
                new_free.append(Rect(used.right, fr.y, fr.right - used.right, fr.h))
            if used.y > fr.y + EPS:  # bottom slice
                new_free.append(Rect(fr.x, fr.y, fr.w, used.y - fr.y))
            if used.top < fr.top - EPS:  # top slice
                new_free.append(Rect(fr.x, used.top, fr.w, fr.top - used.top))
        self.free = new_free

    def _prune_free(self) -> None:
        pruned: list[Rect] = []
        for i, a in enumerate(self.free):
            if a.w <= EPS or a.h <= EPS:
                continue
            redundant = False
            for j, b in enumerate(self.free):
                if i != j and b.contains(a) and not (a.contains(b) and j > i):
                    redundant = True
                    break
            if not redundant:
                pruned.append(a)
        self.free = pruned


def nest_identical_parts(
    piece_length: float,
    piece_width: float,
    part_length: float,
    part_width: float,
    max_parts: int | None = None,
    kerf_mm: float = 3.0,
    edge_margin_mm: float = 5.0,
    allow_rotation: bool = True,
    defects: list[tuple[float, float, float, float]] = (),
) -> list[Placement]:
    """Place as many identical parts as fit on one piece (optionally capped).

    defects: (x, y, length, width) areas in piece coordinates that must not be cut
    (rust patches, holes, cracks). A kerf-wide gap is kept in front of each defect.
    """
    usable_w = piece_length - 2 * edge_margin_mm + kerf_mm
    usable_h = piece_width - 2 * edge_margin_mm + kerf_mm
    if usable_w <= 0 or usable_h <= 0:
        return []
    pw, ph = part_length + kerf_mm, part_width + kerf_mm
    packer = MaxRectsBin(usable_w, usable_h, allow_rotation)
    for dx, dy, dw, dh in defects:
        x0, y0 = max(0.0, dx - edge_margin_mm - kerf_mm), max(0.0, dy - edge_margin_mm - kerf_mm)
        x1, y1 = dx - edge_margin_mm + dw, dy - edge_margin_mm + dh
        if x1 > x0 and y1 > y0:
            packer.block(Rect(x0, y0, x1 - x0, y1 - y0))
    out: list[Placement] = []
    while max_parts is None or len(out) < max_parts:
        res = packer.insert(pw, ph)
        if res is None:
            break
        node, rotated = res
        L, W = (part_width, part_length) if rotated else (part_length, part_width)
        out.append(Placement(node.x + edge_margin_mm, node.y + edge_margin_mm, L, W, rotated))
    return out


def verify_placements(
    piece_length: float,
    piece_width: float,
    placements: list[Placement],
    kerf_mm: float,
    edge_margin_mm: float,
    defects: list[tuple[float, float, float, float]] = (),
) -> list[str]:
    """Independent check of a nest. Returns a list of problems (empty = valid).

    The engine runs this on its own output so a bug in the packer can never
    silently produce an impossible cutting plan.
    """
    problems: list[str] = []
    lo_x, lo_y = edge_margin_mm, edge_margin_mm
    hi_x, hi_y = piece_length - edge_margin_mm, piece_width - edge_margin_mm
    for i, p in enumerate(placements):
        if p.x < lo_x - EPS or p.y < lo_y - EPS or p.x + p.length > hi_x + EPS or p.y + p.width > hi_y + EPS:
            problems.append(f"part {i} outside usable area")
    for i, p in enumerate(placements):
        for dx, dy, dw, dh in defects:
            if Rect(p.x, p.y, p.length, p.width).intersects(Rect(dx, dy, dw, dh)):
                problems.append(f"part {i} overlaps a defect")
    grown = [Rect(p.x, p.y, p.length + kerf_mm, p.width + kerf_mm) for p in placements]
    for i in range(len(grown)):
        for j in range(i + 1, len(grown)):
            if grown[i].intersects(grown[j]):
                problems.append(f"parts {i} and {j} overlap (kerf {kerf_mm} mm)")
    return problems

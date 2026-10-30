#!/usr/bin/env python3
"""Build Super Nova's own fonts from scratch: every glyph below is drawn here, nothing is copied.

    pip install fonttools && python scripts/make_fonts.py

Writes into nebula/assets/fonts/:
  SupernovaDisplay.ttf  geometric monoline letters with chamfered corners (lowercase are small capitals)
  Constellation.ttf     the same skeletons as thin lines between bright star nodes, like a star chart
"""
from __future__ import annotations

import math
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

OUT = Path(__file__).resolve().parent.parent / "nebula" / "assets" / "fonts"
EM, ASC, DESC = 1000, 860, -240


# ------------------------------------------------------------------ shapes
def disc(cx: float, cy: float, r: float, n: int = 16) -> list:
    """A filled circle as a clockwise polygon (TrueType's outer contours run clockwise)."""
    return [(cx + r * math.cos(-2 * math.pi * i / n), cy + r * math.sin(-2 * math.pi * i / n)) for i in range(n)]


def bar(p, q, r: float) -> list:
    """The rectangle around segment p-q, ``r`` either side, clockwise."""
    (x1, y1), (x2, y2) = p, q
    length = math.hypot(x2 - x1, y2 - y1) or 1
    nx, ny = -(y2 - y1) / length * r, (x2 - x1) / length * r
    pts = [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))
    return pts if area < 0 else pts[::-1]


def draw(pen, polygons) -> None:
    for poly in polygons:
        pen.moveTo((round(poly[0][0]), round(poly[0][1])))
        for x, y in poly[1:]:
            pen.lineTo((round(x), round(y)))
        pen.closePath()


def build(name: str, style: str, glyphs: dict, path: Path) -> None:
    """``glyphs``: char -> (advance width, polygons). Overlapping polygons fill together (non-zero winding)."""
    order = [".notdef", "space"] + [f"uni{ord(c):04X}" for c in glyphs if c != " "]
    fb = FontBuilder(EM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): ("space" if c == " " else f"uni{ord(c):04X}") for c in glyphs})
    shapes, metrics = {}, {}
    for gname in order:
        pen = TTGlyphPen(None)
        if gname == ".notdef":
            draw(pen, [[(50, 0), (50, 700), (450, 700), (450, 0)][::-1]])
            adv = 500
        elif gname == "space":
            adv = glyphs[" "][0]
        else:
            adv, polys = glyphs[chr(int(gname[3:], 16))]
            draw(pen, polys)
        shapes[gname] = pen.glyph()
        metrics[gname] = (adv, 0)
    fb.setupGlyf(shapes)
    fb.setupHorizontalMetrics({g: (adv, shapes[g].xMin if hasattr(shapes[g], "xMin") else 0)
                               for g, (adv, _l) in metrics.items()})
    fb.setupHorizontalHeader(ascent=ASC, descent=DESC)
    fb.setupNameTable({"familyName": name, "styleName": style, "uniqueFontIdentifier": f"SuperNova:{name}",
                       "fullName": f"{name} {style}", "psName": f"{name.replace(' ', '')}-{style}",
                       "version": "Version 1.000", "copyright": "Made for Super Nova"})
    fb.setupOS2(sTypoAscender=ASC, sTypoDescender=DESC, usWinAscent=ASC, usWinDescent=-DESC, sCapHeight=700,
                sxHeight=504)
    fb.setupPost()
    fb.save(str(path))


# ------------------------------------------------------- Supernova Display
H, C, R = 700, 105, 44  # cap height, chamfer, half the stroke


def stroke_glyph(lines: list, width: float, scale: tuple = (1.0, 1.0), r: float = R, node: float = R,
                 side: float = 70) -> tuple:
    """Polylines drawn with half-width ``r``; every vertex gets a disc of radius ``node``."""
    sx, sy = scale
    polys = []
    for line in lines:
        pts = [(x * sx, y * sy) for x, y in line]
        for p, q in zip(pts, pts[1:]):
            polys.append(bar(p, q, r))
        polys += [disc(x, y, node, 20) for x, y in pts]
    return width * sx + 2 * side, [[(x + side, y) for x, y in poly] for poly in polys]


def skeletons() -> tuple[dict, dict, dict]:
    """The letter, digit and mark skeletons both faces are drawn from: (width, polylines) per character."""
    W = 500
    o = [(C, 0), (0, C), (0, H - C), (C, H), (W - C, H), (W, H - C), (W, C), (W - C, 0), (C, 0)]
    caps = {
        "A": (W, [[(0, 0), (0, H - C), (C, H), (W - C, H), (W, H - C), (W, 0)], [(0, 330), (W, 330)]]),
        "B": (W, [[(0, 0), (0, H), (W - C, H), (W, H - C), (W, 430), (W - 80, 350), (0, 350)],
                  [(W - 80, 350), (W, 270), (W, C), (W - C, 0), (0, 0)]]),
        "C": (W, [[(W, H), (C, H), (0, H - C), (0, C), (C, 0), (W, 0)]]),
        "D": (W, [[(0, 0), (0, H), (W - C, H), (W, H - C), (W, C), (W - C, 0), (0, 0)]]),
        "E": (W - 40, [[(W - 40, H), (0, H), (0, 0), (W - 40, 0)], [(0, 350), (W - 130, 350)]]),
        "F": (W - 40, [[(W - 40, H), (0, H), (0, 0)], [(0, 350), (W - 130, 350)]]),
        "G": (W, [[(W, H), (C, H), (0, H - C), (0, C), (C, 0), (W, 0), (W, 330), (W - 200, 330)]]),
        "H": (W, [[(0, 0), (0, H)], [(W, 0), (W, H)], [(0, 350), (W, 350)]]),
        "I": (0, [[(0, 0), (0, H)]]),
        "J": (W - 60, [[(W - 60, H), (W - 60, C), (W - 60 - C, 0), (C, 0), (0, C), (0, 190)]]),
        "K": (W, [[(0, 0), (0, H)], [(W, H), (0, 350), (W, 0)]]),
        "L": (W - 60, [[(0, H), (0, 0), (W - 60, 0)]]),
        "M": (600, [[(0, 0), (0, H), (300, 360), (600, H), (600, 0)]]),
        "N": (W, [[(0, 0), (0, H), (W, 0), (W, H)]]),
        "O": (W, [o]),
        "P": (W, [[(0, 0), (0, H), (W - C, H), (W, H - C), (W, 350 + C), (W - C, 350), (0, 350)]]),
        "Q": (W, [o, [(W - 150, 150), (W + 40, -40)]]),
        "R": (W, [[(0, 0), (0, H), (W - C, H), (W, H - C), (W, 350 + C), (W - C, 350), (0, 350)],
                  [(200, 350), (W, 0)]]),
        "S": (W, [[(W, H), (C, H), (0, H - C), (0, 350 + C), (C, 350), (W - C, 350), (W, 350 - C), (W, C),
                   (W - C, 0), (0, 0)]]),
        "T": (W, [[(0, H), (W, H)], [(W / 2, H), (W / 2, 0)]]),
        "U": (W, [[(0, H), (0, C), (C, 0), (W - C, 0), (W, C), (W, H)]]),
        "V": (W, [[(0, H), (W / 2, 0), (W, H)]]),
        "W": (680, [[(0, H), (170, 0), (340, 460), (510, 0), (680, H)]]),
        "X": (W, [[(0, H), (W, 0)], [(W, H), (0, 0)]]),
        "Y": (W, [[(0, H), (W / 2, 350), (W, H)], [(W / 2, 350), (W / 2, 0)]]),
        "Z": (W, [[(0, H), (W, H), (0, 0), (W, 0)]]),
    }
    D = 440  # digits are a little narrower
    od = [(C, 0), (0, C), (0, H - C), (C, H), (D - C, H), (D, H - C), (D, C), (D - C, 0), (C, 0)]
    digits = {
        "0": (D, [od, [(C, C + 40), (D - C, H - C - 40)]]),
        "1": (260, [[(40, H - 130), (170, H), (170, 0)], [(20, 0), (300, 0)]]),
        "2": (D, [[(0, H - C), (C, H), (D - C, H), (D, H - C), (D, 430), (0, 130), (0, 0), (D, 0)]]),
        "3": (D, [[(0, H), (D - C, H), (D, H - C), (D, 350 + C), (D - C, 350), (110, 350)],
                  [(D - C, 350), (D, 350 - C), (D, C), (D - C, 0), (0, 0)]]),
        "4": (D, [[(D - 90, 0), (D - 90, H), (0, 220), (D, 220)]]),
        "5": (D, [[(D, H), (0, H), (0, 390), (D - C, 390), (D, 390 - C), (D, C), (D - C, 0), (0, 0)]]),
        "6": (D, [[(D - 30, H), (C, H), (0, H - C), (0, C), (C, 0), (D - C, 0), (D, C), (D, 390 - C), (D - C, 390),
                   (0, 390)]]),
        "7": (D, [[(0, H), (D, H), (D, H - 90), (140, 0)]]),
        "8": (D, [[(C, 350), (0, 350 + C), (0, H - C), (C, H), (D - C, H), (D, H - C), (D, 350 + C), (D - C, 350),
                   (C, 350), (0, 350 - C), (0, C), (C, 0), (D - C, 0), (D, C), (D, 350 - C), (D - C, 350)]]),
        "9": (D, [[(30, 0), (D - C, 0), (D, C), (D, H - C), (D - C, H), (C, H), (0, H - C), (0, 310 + C), (C, 310),
                   (D, 310)]]),
    }
    dot = lambda y: [[(0, y), (0, y)]]  # noqa: E731
    marks = {
        ".": (0, dot(0)), ",": (40, [[(40, 30), (0, -110)]]), ":": (0, dot(0) + dot(380)),
        ";": (40, [[(40, 30), (0, -110)]] + [[(40, 380), (40, 380)]]), "-": (260, [[(0, 330), (260, 330)]]),
        "!": (0, [[(0, H), (0, 210)]] + dot(0)), "'": (0, [[(0, H), (0, 520)]]),
        "’": (40, [[(40, H), (0, 540)]]), '"': (110, [[(0, H), (0, 520)], [(110, H), (110, 520)]]),
        "?": (400, [[(0, H - C), (C, H), (400 - C, H), (400, H - C), (400, 400), (190, 300), (190, 200)],
                    [(190, 0), (190, 0)]]),
        "/": (320, [[(0, -40), (320, H + 40)]]), "+": (360, [[(0, 330), (360, 330)], [(180, 150), (180, 510)]]),
        "(": (120, [[(120, H + 40), (0, H - 120), (0, 80), (120, -40)]]),
        ")": (120, [[(0, H + 40), (120, H - 120), (120, 80), (0, -40)]]),
        "&": (520, [[(520, 0), (90, 420), (90, H - 70), (160, H), (300, H), (370, H - 70), (370, 560),
                     (0, 230), (0, 80), (80, 0), (300, 0), (480, 200)]]),
        "·": (0, dot(330)), "•": (0, dot(330)), "_": (500, [[(0, -100), (500, -100)]]),
        "#": (480, [[(140, 0), (190, H)], [(330, 0), (380, H)], [(40, 240), (480, 240)], [(0, 460), (440, 460)]]),
        "%": (520, [[(0, 0), (520, H)], [(60, H - 60), (60, H - 60)], [(460, 60), (460, 60)]]),
        "*": (360, [[(180, H), (180, 360)], [(30, 620), (330, 440)], [(30, 440), (330, 620)]]),
        "=": (360, [[(0, 250), (360, 250)], [(0, 420), (360, 420)]]),
        "<": (360, [[(360, 600), (0, 330), (360, 60)]]), ">": (360, [[(0, 600), (360, 330), (0, 60)]]),
        "@": (640, [[(450, 220), (450, 470), (180, 470), (180, 220), (450, 220), (580, 220), (640, 300),
                     (640, H - C), (640 - C, H), (C, H), (0, H - C), (0, C), (C, 0), (560, 0)]]),
    }
    return caps, digits, marks


def display_glyphs() -> dict:
    caps, digits, marks = skeletons()
    out = {" ": (260, [])}
    for ch, (w, lines) in {**caps, **digits, **marks}.items():
        out[ch] = stroke_glyph(lines, w)
    for ch, (w, lines) in caps.items():  # lowercase: small capitals
        out[ch.lower()] = stroke_glyph(lines, w, (0.9, 0.72))
    return out


def constellation_glyphs() -> dict:
    """Thin lines joining star nodes at every corner: each letter is a little constellation."""
    caps, digits, marks = skeletons()
    out = {" ": (300, [])}
    for ch, (w, lines) in {**caps, **digits, **marks}.items():
        out[ch] = stroke_glyph(lines, w, r=20, node=44, side=125)  # roomy: it's read small in the sidebar
    for ch, (w, lines) in caps.items():  # an all-capitals face
        out[ch.lower()] = out[ch]
    return out


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build("Supernova Display", "Regular", display_glyphs(), OUT / "SupernovaDisplay.ttf")
    build("Constellation", "Regular", constellation_glyphs(), OUT / "Constellation.ttf")
    print("wrote", OUT / "SupernovaDisplay.ttf", "and", OUT / "Constellation.ttf")

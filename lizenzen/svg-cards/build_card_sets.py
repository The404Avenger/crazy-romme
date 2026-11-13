#!/usr/bin/env python3
"""Wandelt die Kartensätze aus `Kartenbilder/` in PNG-Bilder für die App um.

Aufruf (im Projektordner):  python3 tools/build_card_sets.py
Braucht `rsvg-convert` (brew install librsvg) und Pillow.

- Die drei Poker-Sätze (cards.revk.uk, CC0): Eckbuchstaben Q/J werden in der SVG-Datei durch
  ein „D“ und ein „B“ im gleichen Strichstil ersetzt. Die Dateien in `Kartenbilder/` sind bereits
  überarbeitet (siehe `Kartenbilder/QUELLEN.md`). Joker und Rückseiten werden nicht übernommen –
  die App zeichnet ihren eigenen Joker.
- SVG-cards 2.0.1 (David Bellot, LGPL 2.1): Einzelkarten werden aus der Sammeldatei geschnitten,
  die Eckbuchstaben Q/J im Bild durch „D“/„B“ ersetzt. Die beiden Joker werden übernommen.

Ergebnis: RommeApp/Resources/Assets.xcassets/Cards/<satz>-<farbe><rang>.imageset
(Farbe C/D/H/S, Rang 1–13; Joker: <satz>-joker-black / -joker-red).
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "Kartenbilder"
TARGET = ROOT / "RommeApp/Resources/Assets.xcassets/Cards"
# Wohin gerade gebaut wird: ein Zwischenordner, der TARGET erst nach Erfolg ersetzt (siehe `main`).
OUTPUT = TARGET
WIDTH, HEIGHT = 270, 378  # 90 × 126 Punkte bei 3-facher Auflösung

SUITS = "CDHS"
REVK_RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "T", 11: "J", 12: "Q", 13: "K"}
REVK_SETS = {"double": "poker-double-qr", "ghost": "poker-ghost-qr", "plain": "poker-PlainCourt-box-qr"}

# Strich-Buchstaben im Stil der revk-Karten (Koordinaten −500…500, Strichbreite wie im Original).
LETTER_D = "M-285 -460L40 -460C250 -460 300 -230 300 0C300 230 250 460 40 460L-285 460M-185 -460L-185 460"
LETTER_B = ("M-285 -460L60 -460C230 -460 270 -340 270 -235C270 -110 200 -20 40 -20L-185 -20"
            "M40 -20C240 -20 300 100 300 220C300 360 230 460 60 460L-285 460M-185 -460L-185 460")


def render(svg: Path, out: Path, export_id: str | None = None) -> None:
    args = ["rsvg-convert", "-w", str(WIDTH), "-h", str(HEIGHT), str(svg), "-o", str(out)]
    if export_id:
        args[1:1] = [f"--export-id={export_id}"]
    subprocess.run(args, check=True)


def save_imageset(png: Path, name: str) -> None:
    folder = OUTPUT / f"{name}.imageset"
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.open(png).convert("RGBA")
    # Kleinere Datei ohne sichtbaren Unterschied.
    image.quantize(colors=256, method=2).save(folder / f"{name}.png", optimize=True)
    (folder / "Contents.json").write_text(json.dumps({
        "images": [{"filename": f"{name}.png", "idiom": "universal"}],
        "info": {"author": "xcode", "version": 1},
    }, indent=2))


def german_letters(svg_text: str) -> str:
    """Ersetzt in den Rang-Symbolen V?Q / V?J den Pfad durch D bzw. B."""
    def replace(match: re.Match, letter: str) -> str:
        # Nur das d-Attribut des Pfads (nicht „id=“, das auch auf d=" endet).
        return re.sub(r'(<path\s[^>]*?\bd=)"[^"]*"', lambda p: f'{p.group(1)}"{letter}"', match.group(0), count=1)
    svg_text = re.sub(r'<symbol id="V[CDHS]Q".*?</symbol>', lambda m: replace(m, LETTER_D), svg_text, flags=re.S)
    svg_text = re.sub(r'<symbol id="V[CDHS]J".*?</symbol>', lambda m: replace(m, LETTER_B), svg_text, flags=re.S)
    return svg_text


def build_revk(work: Path) -> None:
    for name, folder in REVK_SETS.items():
        for suit in SUITS:
            for rank, code in REVK_RANKS.items():
                svg = (SOURCE / folder / f"{code}{suit}.svg").read_text()
                patched = work / f"{name}-{code}{suit}.svg"
                patched.write_text(german_letters(svg))
                png = work / f"{name}-{suit}{rank}.png"
                render(patched, png)
                save_imageset(png, f"{name}-{suit}{rank}")
        print(f"{name}: 52 Karten")


BELLOT_SUITS = {"C": "club", "D": "diamond", "H": "heart", "S": "spade"}
BELLOT_RANKS = {1: "1", 11: "jack", 12: "queen", 13: "king", **{n: str(n) for n in range(2, 11)}}


INDEX_FONT = "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf"


PIP_RATIO = 0.82      # Farbzeichen in der Mitte der Zahlenkarten etwas kleiner
COURT_FIGURE_SCALE = 0.83  # Figur der Bildkarten schmaler, damit die Ecken frei sind
ACE_SCALE = 5.2       # Farbzeichen auf dem Ass (Vorlage: 2,4)
JOKER_RATIO = 0.84    # Joker-Figur kleiner, damit das Wort „JOKER“ Platz hat
PIP_HALF = (6.0, 6.5)  # ungefähre halbe Größe eines Farbzeichens (für Skalierung um die Mitte)


def group_end(text: str, start: int) -> int:
    """Ende der <g>-Gruppe, die bei `start` beginnt (verschachtelte Gruppen beachtet)."""
    depth = 0
    for m in re.finditer(r"<g\b|</g>", text[start:]):
        depth += -1 if m.group(0) == "</g>" else 1
        if depth == 0:
            return start + m.end()
    raise ValueError("Gruppe ohne Ende")


def court_layout(group: str) -> str:
    """Bildkarte: Rahmen, Aussparungen, alle Farb- und Eckzeichen entfernen und die Figur um die
    Kartenmitte auf COURT_FIGURE_SCALE verkleinern."""
    frame = re.search(r'<g fill="none" stroke="black" stroke-width="1">((?:\s*<path [^>]*/>)+)\s*</g>', group)
    numbers = [float(n) for n in re.findall(r"[-\d.]+", " ".join(re.findall(r'd="([^"]*)"', frame.group(1))))]
    xs, ys = numbers[0::2], numbers[1::2]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    group = group.replace(frame.group(0), "", 1)
    group = re.sub(r'<rect [^>]*fill="white"\s*/>', "", group)
    group = re.sub(r'<use xlink:href="#(?:spade|heart|club|diamond|king|queen|jack)"[^>]*/>', "", group)
    k = COURT_FIGURE_SCALE
    base = re.search(r'<use xlink:href="#base"[^>]*/>', group)
    inner_start, inner_end = base.end(), group.rfind("</g>")
    wrapped = f'<g transform="translate({cx * (1 - k):.3f},{cy * (1 - k):.3f}) scale({k})">{group[inner_start:inner_end]}</g>'
    return group[:inner_start] + wrapped + group[inner_end:]


def patch_bellot_svg(text: str) -> str:
    """Passt die Sammeldatei an, bevor sie gerendert wird:
    - alte Eckzeichen (schmale Schrift) entfernen – die App setzt eigene,
    - große Farbzeichen auf Zahlenkarten (2–10) um ihre Mitte verkleinern,
    - Joker-Figur verkleinern und nach unten rechts rücken."""

    def shrink_pips(group: re.Match, ratio: float = PIP_RATIO) -> str:
        def pip(use: re.Match) -> str:
            attrs = use.group(0)
            x = float(re.search(r'\bx="([-\d.]+)"', attrs).group(1))
            y = float(re.search(r'\by="([-\d.]+)"', attrs).group(1))
            cx, cy = x + PIP_HALF[0], y + PIP_HALF[1]
            k = 2.4
            new = f'translate({k * (1 - ratio) * cx:.3f},{k * (1 - ratio) * cy:.3f}) scale({k * ratio:.4f})'
            return attrs.replace("scale(2.4)", new)
        return re.sub(r'<use xlink:href="#(?:spade|heart|club|diamond)"[^>]*scale\(2\.4\)[^>]*/>', pip, group.group(0))

    # Dicker schwarzer Kartenrand weg – die App zeichnet ihren eigenen feinen Rand.
    text = text.replace('style="fill:#FFFFFF;stroke:#000000;stroke-width:2.5"', 'style="fill:#FFFFFF;stroke:none"')
    pip_use = r'<use xlink:href="#(?:spade|heart|club|diamond)"[^>]*scale\(2\.4\)[^>]*/>'

    def xy(use: str) -> tuple[str, str]:
        return re.search(r'\bx="([-\d.]+)"', use).group(1), re.search(r'\by="([-\d.]+)"', use).group(1)

    def big_ace(group: re.Match) -> str:
        """Ass: das Farbzeichen in der Mitte deutlich größer (an derselben Stelle)."""
        def pip(use: re.Match) -> str:
            attrs = use.group(0)
            x, y = xy(attrs)
            nx, ny = f"{float(x) * 2.4 / ACE_SCALE:.4f}", f"{float(y) * 2.4 / ACE_SCALE:.4f}"
            return attrs.replace(x, nx).replace(y, ny).replace("scale(2.4)", f"scale({ACE_SCALE})")
        return re.sub(pip_use, pip, group.group(0))

    def center_odd_pip(group: re.Match) -> str:
        """7 und 9: das einzelne Farbzeichen der Mittelspalte genau in die Kartenmitte
        (sonst sitzt es in der oberen Hälfte und die Karte ist nicht punktsymmetrisch)."""
        uses = re.findall(pip_use, group.group(0))
        columns = [xy(u)[0] for u in uses]
        rows = [float(xy(u)[1]) for u in uses]
        single = next(u for u in uses if columns.count(xy(u)[0]) == 1)
        # Gedrehte Zeichen liegen 2 Einheiten über ihrem y-Wert, ungedrehte 2 darunter.
        y = xy(single)[1]
        return group.group(0).replace(single, single.replace(y, f"{(min(rows) + max(rows)) / 2 - 2:.3f}"))

    text = re.sub(r'<g id="1_(?:club|diamond|heart|spade)">.*?</g>', big_ace, text, flags=re.S)
    text = re.sub(r'<g id="[79]_(?:club|diamond|heart|spade)">.*?</g>', center_odd_pip, text, flags=re.S)
    text = re.sub(r'<g id="(?:[2-9]|10)_(?:club|diamond|heart|spade)">.*?</g>', shrink_pips, text, flags=re.S)
    # Bildkarten: Layout wie ein klassisches französisches Blatt – Figur schmaler und frei in der Mitte,
    # ohne Rahmen; Eckzeichen und Farbzeichen setzt `draw_bellot_court` (alle vier Ecken).
    for match in reversed(list(re.finditer(r'<g id="(?:king|queen|jack)_(?:club|diamond|heart|spade)">', text))):
        start, end = match.start(), group_end(text, match.start())
        text = text[:start] + court_layout(text[start:end]) + text[end:]
    # Erst jetzt die alten Eckzeichen entfernen (die Suche oben braucht sie als Grenze).
    text = re.sub(r'<use xlink:href="#(?:n_\d+|ace|king|queen|jack)"[^>]*/>', "", text)
    # Joker: Figur um einen Punkt unten rechts verkleinern (Karte ca. 167 × 243 Einheiten).
    def joker(match: re.Match) -> str:
        group = match.group(0)
        base = re.search(r'<use xlink:href="#base" x="([-\d.]+)" y="([-\d.]+)"', group)
        bx, by = float(base.group(1)), float(base.group(2))
        px, py = bx + 150, by - 20
        return group.replace('<g id="Full_joker">',
            f'<g id="Full_joker" transform="translate({px * (1 - JOKER_RATIO):.3f},{py * (1 - JOKER_RATIO):.3f}) scale({JOKER_RATIO})">')
    text = re.sub(r'<g id="(?:black|red)_joker">.*?<g id="Full_joker">', joker, text, flags=re.S)
    return text


def draw_bellot_index(png: Path, letter: str, red: bool) -> None:
    """Setzt das Eckzeichen (oben links, unten rechts gedreht) in einer breiten, gut lesbaren Schrift.
    Die alten Zeichen sind schon aus der Vorlage entfernt – nichts wird überdeckt."""
    image = Image.open(png).convert("RGBA")
    w, h = image.size
    court = letter in ("B", "D", "K")
    # Bildkarten: nur bis zum Rahmen der Figur. Zahlenkarten: mehr Platz, die Farbzeichen sind kleiner.
    left, top = w * 0.03, h * 0.022
    max_w = w * (0.14 if court else 0.16)
    max_h = h * 0.13
    color = (220, 30, 30, 255) if red else (0, 0, 0, 255)
    size = int(max_h * 1.4)
    while size > 8:
        font = ImageFont.truetype(INDEX_FONT, size)
        x0, y0, x1, y1 = font.getbbox(letter)
        if x1 - x0 <= max_w and y1 - y0 <= max_h:
            break
        size -= 1
    for rotated in (False, True):
        if rotated:
            image = image.rotate(180)
        draw = ImageDraw.Draw(image)
        x0, y0, _, _ = font.getbbox(letter)
        draw.text((left - x0, top - y0), letter, font=font, fill=color)
        if rotated:
            image = image.rotate(180)
    image.save(png)


SUIT_FONT = "/System/Library/Fonts/Apple Symbols.ttf"
SUIT_GLYPHS = {"C": "\u2663", "D": "\u2666", "H": "\u2665", "S": "\u2660"}


def fitted_font(path: str, text: str, max_w: float, max_h: float) -> ImageFont.FreeTypeFont:
    size = int(max_h * 1.5)
    while size > 6:
        font = ImageFont.truetype(path, size)
        x0, y0, x1, y1 = font.getbbox(text)
        if x1 - x0 <= max_w and y1 - y0 <= max_h:
            return font
        size -= 1
    return ImageFont.truetype(path, size)


def draw_glyph(draw: ImageDraw.ImageDraw, font, text: str, left: float, top: float, color) -> None:
    x0, y0, _, _ = font.getbbox(text)
    draw.text((left - x0, top - y0), text, font=font, fill=color)


def draw_bellot_court(png: Path, letter: str, suit: str) -> None:
    """Bildkarte wie im klassischen französischen Blatt: in allen vier Ecken Buchstabe und kleines
    Farbzeichen darunter, dazu oben links (und gedreht unten rechts) ein großes Farbzeichen."""
    image = Image.open(png).convert("RGBA")
    w, h = image.size
    red = suit in "DH"
    color = (220, 30, 30, 255) if red else (0, 0, 0, 255)
    glyph = SUIT_GLYPHS[suit]
    letter_font = fitted_font(INDEX_FONT, letter, w * 0.11, h * 0.085)
    small_font = fitted_font(SUIT_FONT, glyph, w * 0.09, h * 0.06)
    big_font = fitted_font(SUIT_FONT, glyph, w * 0.17, h * 0.1)
    for rotated in (False, True):
        if rotated:
            image = image.rotate(180)
        draw = ImageDraw.Draw(image)
        for side in ("left", "right"):
            lx0, _, lx1, ly1 = letter_font.getbbox(letter)
            lw, lh = lx1 - lx0, ly1 - letter_font.getbbox(letter)[1]
            sx0, sy0, sx1, sy1 = small_font.getbbox(glyph)
            sw = sx1 - sx0
            column = w * 0.11
            left = w * 0.035 if side == "left" else w - w * 0.035 - column
            draw_glyph(draw, letter_font, letter, left + (column - lw) / 2, h * 0.025, color)
            draw_glyph(draw, small_font, glyph, left + (column - sw) / 2, h * 0.025 + lh + h * 0.012, color)
        # Großes Farbzeichen rechts neben der linken Ecke, senkrecht mittig zwischen Oberkante des
        # Buchstabens und Unterkante des kleinen Farbzeichens.
        index_top = h * 0.025
        index_bottom = index_top + lh + h * 0.012 + (sy1 - sy0)
        bx0, by0, bx1, by1 = big_font.getbbox(glyph)
        draw_glyph(draw, big_font, glyph, w * 0.165, (index_top + index_bottom) / 2 - (by1 - by0) / 2, color)
        if rotated:
            image = image.rotate(180)
    image.save(png)


def build_bellot(work: Path) -> None:
    svg = work / "svg-cards.svg"
    svg.write_text(patch_bellot_svg((SOURCE / "SVG-cards-2.0.1/svg-cards.svg").read_text()))
    for suit, suit_name in BELLOT_SUITS.items():
        for rank, rank_name in BELLOT_RANKS.items():
            png = work / f"bellot-{suit}{rank}.png"
            render(svg, png, f"{rank_name}_{suit_name}")
            letter = {1: "A", 11: "B", 12: "D", 13: "K"}.get(rank, str(rank))
            if rank >= 11:
                draw_bellot_court(png, letter, suit)
            else:
                draw_bellot_index(png, letter, red=suit in "DH")
            save_imageset(png, f"bellot-{suit}{rank}")
    for color in ("black", "red"):
        png = work / f"bellot-joker-{color}.png"
        render(svg, png, f"{color}_joker")
        save_imageset(png, f"bellot-joker-{color}")
    print("bellot: 52 Karten + 2 Joker")


def replace_target(built: Path) -> None:
    """Tauscht die fertigen Kartenbilder ein. Bis dahin bleibt der alte Ordner unangetastet."""
    staged = TARGET.with_name(".Cards-neu")
    old = TARGET.with_name(".Cards-alt")
    for leftover in (staged, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.copytree(built, staged)
    if TARGET.exists():
        TARGET.rename(old)
    staged.rename(TARGET)
    if old.exists():
        shutil.rmtree(old)


def main() -> None:
    global OUTPUT
    if shutil.which("rsvg-convert") is None:
        sys.exit("rsvg-convert fehlt: brew install librsvg")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # Erst in einen Zwischenordner bauen – schlägt etwas fehl, bleiben die alten Kartenbilder erhalten.
        OUTPUT = work / "Cards"
        OUTPUT.mkdir()
        (OUTPUT / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2))
        build_revk(work)
        build_bellot(work)
        replace_target(OUTPUT)


if __name__ == "__main__":
    main()

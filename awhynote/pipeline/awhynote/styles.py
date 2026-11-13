"""Caption/headline typography presets. Every font listed here has the rupee sign (₹)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    name: str
    file: str                       # key into util.FONT_URLS
    wght: int | None = None         # weight axis for variable fonts
    sizes: tuple[int, ...] = (80, 72, 64, 58, 50)
    upper: bool = True
    stroke: int = 5                 # dark outline around plain words
    highlight: str = "box"          # "box" = yellow box + dark text, "text" = yellow text
    pop: bool = True                # newest word pops in
    line_height: float = 1.30
    note: str = ""


STYLES: dict[str, Style] = {s.name: s for s in (
    Style("poppins", "Poppins-ExtraBold.ttf", note="Round, friendly, very legible. Yellow box on the key word."),
    Style("montserrat", "Montserrat-Var.ttf", wght=800, note="Clean geometric sans, the classic short-video look."),
    Style("anton", "Anton-Regular.ttf", sizes=(118, 108, 98, 88, 76), stroke=6, highlight="text", line_height=1.18,
          note="Tall and condensed, punchy. Key word in yellow, no box."),
    Style("bebas", "BebasNeue-Regular.ttf", sizes=(128, 116, 106, 96, 82), stroke=5, line_height=1.12,
          note="Condensed headline caps with a yellow box on the key word."),
)}
DEFAULT_STYLE = "poppins"


def get_style(name: str | None) -> Style:
    return STYLES.get((name or "").strip().lower(), STYLES[DEFAULT_STYLE])

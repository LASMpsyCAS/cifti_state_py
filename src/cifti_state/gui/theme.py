"""Visual theme: an airy blue-green palette and the stylesheet built from it.

The palette runs from a pale sea-glass background through teal to a cooler
blue, so the interface reads as one gradient rather than a set of unrelated
accents.  Every colour is defined once here; the stylesheet, the matplotlib
figures and the cluster table all draw from the same tokens.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Palette", "PALETTE", "stylesheet", "matplotlib_rc", "CLUSTER_CYCLE"]


@dataclass(frozen=True)
class Palette:
    # -- grounds ------------------------------------------------------------ #
    canvas: str = "#EDF5F4"        # window background, palest sea glass
    surface: str = "#FFFFFF"       # cards
    surface_alt: str = "#F5FAFA"   # table stripes, inset panels
    sunken: str = "#E3EFEE"        # log window, disabled fields

    # -- lines -------------------------------------------------------------- #
    border: str = "#CFE3E1"
    border_strong: str = "#AECFCC"
    divider: str = "#DDECEA"

    # -- the blue-green run ------------------------------------------------- #
    teal_deep: str = "#0F6E6A"
    teal: str = "#189E97"          # primary
    teal_bright: str = "#2BB8AE"
    aqua: str = "#5FD3C4"
    mist: str = "#A9E5DC"
    sea: str = "#2E8FA8"           # the blue end
    sea_deep: str = "#1D6A85"
    sky: str = "#6FC0D6"

    # -- text --------------------------------------------------------------- #
    ink: str = "#123338"
    ink_soft: str = "#2A4E55"
    muted: str = "#5B8087"
    faint: str = "#8AA9AE"
    on_accent: str = "#FFFFFF"

    # -- status ------------------------------------------------------------- #
    ok: str = "#2E9E7B"
    warn: str = "#C88A3C"
    danger: str = "#C4574F"
    info: str = "#2E8FA8"

    # -- data --------------------------------------------------------------- #
    positive: str = "#E4674A"      # warm, so it reads against the cool chrome
    negative: str = "#3E86C4"
    selection: str = "#CDEFE8"


PALETTE = Palette()

#: Qualitative colours for cluster rows/markers, tuned to sit on the pale ground.
CLUSTER_CYCLE = (
    "#189E97", "#2E8FA8", "#5FD3C4", "#6FC0D6", "#0F6E6A",
    "#4BA3B8", "#7FD8CE", "#3E9C8E", "#87C9DA", "#1D6A85",
)


def matplotlib_rc(palette: Palette = PALETTE) -> dict:
    """rcParams so embedded figures share the window's ground and type."""
    return {
        "figure.facecolor": palette.surface,
        "axes.facecolor": palette.surface,
        "savefig.facecolor": palette.surface,
        "text.color": palette.ink,
        "axes.labelcolor": palette.ink,
        "axes.edgecolor": palette.border_strong,
        "xtick.color": palette.muted,
        "ytick.color": palette.muted,
        "font.size": 9,
        # cifti_state.fonts owns font.family / font.sans-serif / axes.unicode_minus
    }


def _chevron(colour: str) -> str:
    """Path to a downward chevron SVG, for the combo box drop-down.

    Qt's stylesheet ``url()`` does not accept data URIs, so the icon is written
    once to a cache directory and referenced by path. Forward slashes work on
    Windows too, which keeps the stylesheet free of escaping.
    """
    name = f"chevron-{colour.lstrip('#').lower()}.svg"
    path = _icon_dir() / name
    if not path.exists():
        path.write_text(
            "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10' "
            "viewBox='0 0 10 10'><path d='M1.6 3.4 5 6.8l3.4-3.4' fill='none' "
            f"stroke='{colour}' stroke-width='1.6' stroke-linecap='round' "
            "stroke-linejoin='round'/></svg>",
            encoding="utf-8",
        )
    return path.as_posix()


def _icon_dir() -> Path:
    directory = Path(tempfile.gettempdir()) / "cifti_state_icons"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def stylesheet(palette: Palette = PALETTE, fonts=None, font_size_px: int = 13) -> str:
    """Build the stylesheet.

    *fonts* is a :class:`cifti_state.fonts.FontChoice`; pass the one the
    application resolved so the stylesheet names the families that were
    actually found, rather than a wish-list Qt would resolve per glyph.

    *font_size_px* must match the pixel size given to
    :func:`cifti_state.fonts.apply_to_qt`.  Two size systems in one interface
    -- points on the widget, pixels in the stylesheet -- means the desktop's
    DPI setting can scale one and not the other, and text starts colliding
    with the box it sits in.
    """
    p = palette
    if fonts is None:
        from ..fonts import resolve_fonts

        fonts = resolve_fonts()
    ui_family = fonts.ui_css
    mono_family = fonts.mono_css
    chevron = _chevron(p.teal)
    chevron_faint = _chevron(p.faint)

    base = int(font_size_px)
    small = max(9, base - 1)
    tiny = max(8, base - 2)
    heading = base + 5
    return f"""
/* ---------------------------------------------------------------- base -- */
QWidget {{
    background: {p.canvas};
    color: {p.ink};
    font-family: {ui_family};
    font-size: {base}px;
}}
QMainWindow, QDialog {{ background: {p.canvas}; }}

QToolTip {{
    background: {p.ink};
    color: #FFFFFF;
    border: none;
    padding: 6px 9px;
    border-radius: 5px;
}}

/* -------------------------------------------------------------- header -- */
#HeaderBar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {p.teal_deep},
                                stop:0.5 {p.teal},
                                stop:1 {p.sea});
    border: none;
}}
/* children of the gradient must not paint the window ground over it */
#HeaderBar QWidget {{ background: transparent; }}
#HeaderBar QLabel {{ background: transparent; }}
#HeaderTitle {{
    color: #FFFFFF;
    font-size: {heading}px;
    font-weight: 600;
    background: transparent;
    letter-spacing: 0.4px;
}}
#HeaderSubtitle {{
    color: rgba(255, 255, 255, 0.82);
    font-size: {small}px;
    background: transparent;
}}
#HeaderChip {{
    color: #FFFFFF;
    background: rgba(255, 255, 255, 0.18);
    border: 1px solid rgba(255, 255, 255, 0.30);
    border-radius: 11px;
    padding: 3px 12px;
    font-size: {tiny}px;
}}

/* --------------------------------------------------------------- cards -- */
#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}
#CardHeader {{ background: transparent; }}
#CardTitle {{
    font-size: {base}px;
    font-weight: 600;
    color: {p.teal_deep};
    background: transparent;
    letter-spacing: 0.2px;
}}
#CardStep {{
    color: #FFFFFF;
    background: {p.teal};
    border-radius: 9px;
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    max-height: 18px;
    font-size: {tiny}px;
    font-weight: 600;
}}
#CardHint {{
    color: {p.muted};
    font-size: {tiny}px;
    background: transparent;
}}
#Divider {{
    background: {p.divider};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

QLabel {{ background: transparent; }}
#FieldLabel {{
    color: {p.ink_soft};
    font-size: {small}px;
    background: transparent;
}}
#Muted   {{ color: {p.muted};  font-size: {tiny}px; background: transparent; }}
#Mono    {{ font-family: {mono_family}; font-size: {tiny}px;
            color: {p.ink_soft}; background: transparent; }}
#ValueStrong {{ color: {p.teal_deep}; font-weight: 600; background: transparent; }}

/* -------------------------------------------------------------- inputs -- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {p.aqua};
    selection-color: {p.ink};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {p.teal_bright};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus {{
    border: 1px solid {p.teal};
    background: #FBFEFE;
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {p.sunken};
    color: {p.faint};
    border-color: {p.border};
}}
QLineEdit[readOnly="true"] {{
    background: {p.surface_alt};
    color: {p.ink_soft};
}}

/* Styling ::drop-down stops Fusion drawing its own arrow, and Qt does not
   render the CSS border-triangle trick, so the chevron is supplied as an SVG. */
QComboBox::drop-down {{
    border: none;
    background: transparent;
    width: 22px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    image: url("{chevron}");
    width: 9px;
    height: 9px;
    margin-right: 7px;
}}
QComboBox::down-arrow:disabled {{ image: url("{chevron_faint}"); }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    selection-background-color: {p.selection};
    selection-color: {p.ink};
    outline: none;
    padding: 3px;
}}

/* The spin buttons are deliberately NOT styled.  Restyling a sub-control makes
   Qt take the full stylesheet drawing path for the whole widget, and on some
   Windows themes the text is then laid out against a box of a different size
   than the one drawn -- digits end up clipped or overlapping.  Fusion draws
   perfectly good arrows; the field just reserves room for them. */
QSpinBox, QDoubleSpinBox {{
    padding-right: 20px;
    min-height: {base + 8}px;
}}

/* ------------------------------------------------------------- buttons -- */
QPushButton {{
    background: {p.surface};
    color: {p.teal_deep};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: {small}px;
}}
QPushButton:hover  {{ background: {p.surface_alt}; border-color: {p.teal_bright}; }}
QPushButton:pressed{{ background: {p.mist}; }}
QPushButton:disabled {{
    background: {p.sunken};
    color: {p.faint};
    border-color: {p.border};
}}

QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {p.teal}, stop:1 {p.sea});
    color: #FFFFFF;
    border: none;
    padding: 8px 18px;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {p.teal_bright}, stop:1 {p.sky});
}}
QPushButton#Primary:pressed {{ background: {p.teal_deep}; }}
QPushButton#Primary:disabled {{
    background: {p.sunken}; color: {p.faint};
}}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {p.teal_bright};
    color: {p.teal_deep};
}}
QPushButton#Ghost:hover {{ background: rgba(95, 211, 196, 0.16); }}
QPushButton#Ghost:disabled {{ border-color: {p.border}; color: {p.faint}; }}

QPushButton#Danger {{ color: {p.danger}; border-color: #E5C4C1; }}
QPushButton#Danger:hover {{ background: #FBF0EF; }}

QPushButton#IconButton {{
    padding: 4px 8px;
    font-size: {tiny}px;
}}

/* ---------------------------------------------------------- checkables -- */
QCheckBox, QRadioButton {{ background: transparent; spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {p.border_strong};
    background: {p.surface};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {p.teal_bright};
}}
QCheckBox::indicator:checked {{
    background: {p.teal};
    border-color: {p.teal};
    image: none;
}}
QRadioButton::indicator:checked {{
    background: {p.surface};
    border: 4px solid {p.teal};
}}

/* -------------------------------------------------------------- tables -- */
QTableView {{
    background: {p.surface};
    alternate-background-color: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: 8px;
    gridline-color: {p.divider};
    selection-background-color: {p.selection};
    selection-color: {p.ink};
    outline: none;
}}
QTableView::item {{ padding: 4px 6px; border: none; }}
QTableView::item:selected {{
    background: {p.selection};
    color: {p.teal_deep};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {p.surface_alt};
    color: {p.ink_soft};
    border: none;
    border-bottom: 1px solid {p.border_strong};
    border-right: 1px solid {p.divider};
    padding: 6px 8px;
    font-weight: 600;
    font-size: {tiny}px;
}}
QHeaderView::section:hover {{ background: {p.mist}; }}
QTableView QTableCornerButton::section {{
    background: {p.surface_alt};
    border: none;
    border-bottom: 1px solid {p.border_strong};
}}

/* ---------------------------------------------------------------- tabs -- */
QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: 8px;
    background: {p.surface};
    top: -1px;
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {p.muted};
    padding: 7px 16px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: {small}px;
}}
QTabBar::tab:hover {{ color: {p.teal_deep}; }}
QTabBar::tab:selected {{
    color: {p.teal_deep};
    font-weight: 600;
    border-bottom: 2px solid {p.teal};
}}

/* ------------------------------------------------------------- scrolls -- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.teal_bright}; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_strong}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.teal_bright}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ------------------------------------------------------------ progress -- */
QProgressBar {{
    background: {p.sunken};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {p.aqua}, stop:1 {p.sea});
}}

/* -------------------------------------------------------------- splits -- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {p.mist}; }}

/* --------------------------------------------------------------- misc  -- */
#StatusBar {{
    background: {p.surface};
    border-top: 1px solid {p.border};
}}
#StatusText {{ color: {p.muted}; font-size: {tiny}px; background: transparent; }}

#LogView {{
    background: {p.sunken};
    border: 1px solid {p.border};
    border-radius: 8px;
    font-family: {mono_family};
    font-size: {tiny}px;
    color: {p.ink_soft};
}}

#Pill {{
    background: {p.mist};
    color: {p.teal_deep};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: {tiny}px;
}}
#PillWarn   {{ background: #F7E7D0; color: #8A5B1E; border-radius: 9px;
               padding: 2px 10px; font-size: {tiny}px; }}
#PillDanger {{ background: #F7DEDC; color: #8E3A34; border-radius: 9px;
               padding: 2px 10px; font-size: {tiny}px; }}
#PillMuted  {{ background: {p.sunken}; color: {p.muted}; border-radius: 9px;
               padding: 2px 10px; font-size: {tiny}px; }}

QMenuBar {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
QMenuBar::item {{ padding: 5px 11px; background: transparent; }}
QMenuBar::item:selected {{ background: {p.mist}; border-radius: 4px; }}
QMenu {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 7px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.selection}; color: {p.teal_deep}; }}
QMenu::separator {{ height: 1px; background: {p.divider}; margin: 4px 8px; }}
"""

"""Typography, resolved in Python rather than left to the machine.

Two things go wrong when fonts are left to whatever the OS picks:

* **Missing glyphs.**  matplotlib's default DejaVu Sans has no CJK, so a
  Chinese path in a figure title comes out as tofu boxes.  Qt's own fallback
  differs again, so the window and the figure disagree.
* **The minus sign.**  matplotlib defaults to ``axes.unicode_minus = True``,
  which draws U+2212 MINUS SIGN.  Plenty of CJK-capable fonts lack that
  codepoint, so every negative number in a figure -- coordinates, thresholds --
  turns into a box.  This is the single most common cause of "the numbers look
  wrong".

So the font is chosen *here*, once, from an explicit ordered list, and the same
resolved family is handed to Qt and to matplotlib.  What the resolution picked
is logged and shown by ``cifti-state check --fonts``, so a machine that renders
differently can be diagnosed instead of guessed at.

Dropping ``.ttf`` / ``.otf`` files into ``resources/fonts/`` makes the result
fully machine-independent: bundled fonts are registered with both toolkits and
take priority over anything installed.

Everything the package reads or writes is UTF-8 (``encoding="utf-8"``, and
``utf-8-sig`` for CSV so Excel on a Chinese Windows opens it without mojibake);
:func:`configure_stdio` also forces UTF-8 on stdout/stderr, because the Windows
console defaults to GBK and would otherwise raise on a path it cannot encode.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "FontChoice",
    "UI_FAMILIES",
    "MONO_FAMILIES",
    "CJK_FAMILIES",
    "bundled_font_dir",
    "configure_stdio",
    "digits_are_sane",
    "SPECIMEN_DIGITS",
    "resolve_fonts",
    "apply_to_qt",
    "numeric_font",
    "apply_numeric_font",
    "apply_numeric_fonts",
    "render_specimen",
    "apply_to_matplotlib",
    "font_report",
]

#: Interface font, most preferred first.  Every entry is a family that ships
#: with a mainstream OS or is commonly installed; the first one actually
#: present wins.  Chinese Windows has Microsoft YaHei UI, so a mixed
#: English/Chinese machine lands on a family that covers both.
#:
#: Deliberately absent: variable fonts installed by hand (Inter, Segoe UI
#: Variable and friends).  Qt's DirectWrite backend picks the wrong named
#: instance from some of them, which shows up as digits drawn with the wrong
#: glyphs -- ``0.1160`` rendered as ``O.ıı ϗ OO``.  A font dropped into
#: ``resources/fonts/`` is still preferred over everything in this list, so a
#: bundled Inter is used; an installed one is not gambled on.
UI_FAMILIES: tuple[str, ...] = (
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    "Microsoft YaHei UI",    # Chinese Windows, covers Latin + CJK
    "Microsoft YaHei",
    "Segoe UI",              # English Windows; no CJK, hence the order above
    "PingFang SC",           # macOS
    "Noto Sans",
    "DejaVu Sans",
    "Arial",
)

#: Fixed-width family for the read-outs and the log, so digits line up.
MONO_FAMILIES: tuple[str, ...] = (
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "Noto Sans Mono CJK SC",
    "DejaVu Sans Mono",
    "Menlo",
    "Courier New",
    "monospace",
)

#: Appended after the UI family so CJK still renders when the UI family is
#: Latin-only (Segoe UI on an English Windows, DejaVu Sans on Linux).
CJK_FAMILIES: tuple[str, ...] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    "PingFang SC",
    "SimHei",
    "WenQuanYi Zen Hei",
    "Arial Unicode MS",
)


@dataclass
class FontChoice:
    """What the resolution settled on, and what it had to choose from."""

    ui: str = "sans-serif"
    mono: str = "monospace"
    cjk: Optional[str] = None
    ui_stack: list[str] = field(default_factory=list)
    mono_stack: list[str] = field(default_factory=list)
    bundled: list[str] = field(default_factory=list)
    available: int = 0
    source: str = "fallback"     # qt | matplotlib | fallback

    @property
    def ui_css(self) -> str:
        """A CSS font-family value for the Qt stylesheet."""
        return ", ".join(f'"{name}"' for name in self.ui_stack) or "sans-serif"

    @property
    def mono_css(self) -> str:
        return ", ".join(f'"{name}"' for name in self.mono_stack) or "monospace"

    def summary(self) -> list[tuple[str, str]]:
        return [
            ("interface", self.ui),
            ("monospace", self.mono),
            ("CJK fallback", self.cjk or "(the interface font covers CJK)"),
            ("bundled", ", ".join(self.bundled) or "none"),
            ("resolved via", self.source),
            ("families seen", str(self.available)),
        ]


_CHOICE: Optional[FontChoice] = None


def bundled_font_dir() -> Path:
    """``resources/fonts/`` next to the package. Drop .ttf/.otf files here."""
    return Path(__file__).resolve().parent.parent.parent / "resources" / "fonts"


# --------------------------------------------------------------------------- #
# encoding
# --------------------------------------------------------------------------- #


def configure_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    The Windows console is GBK by default, so logging a path with characters it
    cannot encode raises ``UnicodeEncodeError`` from inside the logging call --
    which looks like a crash in whatever was running at the time.  ``errors=
    "replace"`` means an unencodable character degrades to a placeholder rather
    than taking the process down.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - a redirected stream may refuse
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #


def resolve_fonts(
    *, force: bool = False, settings=None, ui: str = "", mono: str = ""
) -> FontChoice:
    """Pick the interface, monospace and CJK families. Cached after the first call.

    Uses Qt's font database when a ``QApplication`` exists (that is what the
    window will actually draw with) and matplotlib's otherwise, so the answer is
    right in both a GUI and a headless script.
    """
    global _CHOICE
    if _CHOICE is not None and not force:
        return _CHOICE

    if settings is not None:
        ui = ui or getattr(settings.interface, "ui_font", "")
        mono = mono or getattr(settings.interface, "mono_font", "")

    bundled = _register_bundled_fonts()
    available, source = _available_families()
    lowered = {name.lower(): name for name in available}

    def first_present(candidates: tuple[str, ...], *, digits: bool = True):
        fallback: Optional[str] = None
        for name in candidates:
            match = lowered.get(name.lower())
            if not match:
                continue
            if not digits or digits_are_sane(match):
                return match
            log.warning(
                "skipping the font %r: its digits do not map to distinct "
                "glyphs, so numbers would render incorrectly", match,
            )
            fallback = fallback or match
        return fallback

    # An explicit family from the configuration wins outright, even if the
    # database does not list it -- Qt may still resolve it, and being able to
    # force a family is the point of the setting.
    ui = ui or first_present(tuple(bundled) + UI_FAMILIES) or "sans-serif"
    if ui and ui.lower() in lowered:
        ui = lowered[ui.lower()]
    mono = mono or first_present(MONO_FAMILIES) or "monospace"
    if mono and mono.lower() in lowered:
        mono = lowered[mono.lower()]
    cjk = first_present(CJK_FAMILIES, digits=False)
    if cjk and cjk.lower() == ui.lower():
        cjk = None                      # the UI font already covers CJK

    ui_stack = [ui] + ([cjk] if cjk else []) + ["sans-serif"]
    mono_stack = [mono] + ([cjk] if cjk else []) + ["monospace"]

    _CHOICE = FontChoice(
        ui=ui,
        mono=mono,
        cjk=cjk,
        ui_stack=ui_stack,
        mono_stack=mono_stack,
        bundled=bundled,
        available=len(available),
        source=source,
    )
    log.info(
        "fonts: interface %r, monospace %r, CJK fallback %r (from %s, %d families)",
        ui, mono, cjk or "-", source, len(available),
    )
    return _CHOICE


#: The ten digits, a decimal point and a minus -- everything a spin box shows.
SPECIMEN_DIGITS = "0123456789"


def digits_are_sane(family: str) -> bool:
    """Would numbers drawn in *family* be readable?

    A font can be present and still draw ``0.1160`` as ``O.ıı ϗ OO``: broken
    subsets, and some variable fonts under Qt's DirectWrite backend, hand back
    glyph indices that do not belong to the characters asked for.  The tell is
    cheap to check -- the ten digits must map to ten *distinct*, non-missing
    glyphs, and none of them may share a glyph with a letter.

    Returns ``True`` when the question cannot be answered (no Qt, an old
    PySide6 without :class:`QRawFont`), because refusing every font would be
    worse than the problem being guarded against.
    """
    try:
        from PySide6.QtGui import QFont, QGuiApplication, QRawFont
    except Exception:  # pragma: no cover - PySide6 is optional
        return True
    # Constructing a QFont without a QGuiApplication aborts the process, so
    # this has to be checked rather than caught.
    if QGuiApplication.instance() is None:
        return True
    try:
        raw = QRawFont.fromFont(QFont(family, 12))
        if not raw.isValid():
            return True
        digits = list(raw.glyphIndexesForString(SPECIMEN_DIGITS))
        letters = set(raw.glyphIndexesForString("OolIiSsBg"))
    except Exception:  # pragma: no cover - platform dependent
        return True
    if len(digits) != len(SPECIMEN_DIGITS):
        return False
    if 0 in digits:                       # .notdef -- the glyph is missing
        return False
    if len(set(digits)) != len(digits):   # two digits sharing one glyph
        return False
    return not (set(digits) & letters)    # a digit drawn as a letter


def _register_bundled_fonts() -> list[str]:
    """Load any font files shipped in ``resources/fonts/`` into both toolkits."""
    directory = bundled_font_dir()
    if not directory.exists():
        return []

    files = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in (".ttf", ".otf", ".ttc")
    )
    if not files:
        return []

    names: list[str] = []
    try:
        from PySide6.QtGui import QFontDatabase

        for path in files:
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                names.extend(QFontDatabase.applicationFontFamilies(font_id))
            else:
                log.warning("Qt could not load the bundled font %s", path.name)
    except Exception as exc:  # pragma: no cover - PySide6 is optional
        log.debug("no Qt font registration (%s)", exc)

    try:
        from matplotlib import font_manager

        for path in files:
            font_manager.fontManager.addfont(str(path))
            try:
                names.append(font_manager.FontProperties(fname=str(path)).get_name())
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover
        log.debug("no matplotlib font registration (%s)", exc)

    unique = list(dict.fromkeys(n for n in names if n))
    if unique:
        log.info("registered bundled fonts: %s", ", ".join(unique))
    return unique


def _available_families() -> tuple[list[str], str]:
    try:
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is not None:
            return list(QFontDatabase.families()), "qt"
    except Exception:  # pragma: no cover - PySide6 is optional
        pass
    try:
        from matplotlib import font_manager

        return (
            sorted({f.name for f in font_manager.fontManager.ttflist}),
            "matplotlib",
        )
    except Exception:  # pragma: no cover
        return [], "fallback"


# --------------------------------------------------------------------------- #
# application
# --------------------------------------------------------------------------- #


def apply_to_qt(app, *, pixel_size: int = 13, settings=None) -> FontChoice:
    """Set the application font and locale explicitly.

    Two things are pinned here on purpose:

    * **Pixel size, not point size.**  Points are scaled by the desktop's DPI
      setting, so the same build looks different on two machines and the
      stylesheet (which is written in ``px``) disagrees with the widget font.
      One unit system removes a whole class of layout surprises.
    * **The C locale.**  ``QDoubleSpinBox`` formats through ``QLocale``, which
      on some systems substitutes native digits or a different decimal
      separator.  A scientific tool should show ``0.05``, never ``٠٫٠٥``.
    """
    from PySide6.QtCore import QLocale
    from PySide6.QtGui import QFont

    QLocale.setDefault(QLocale(QLocale.Language.C))

    choice = resolve_fonts(force=True, settings=settings)   # QApplication exists now
    font = QFont(choice.ui)
    font.setPixelSize(int(pixel_size))
    font.setStyleHint(QFont.StyleHint.SansSerif, QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    if choice.cjk:
        # Qt 6 consults these in order when the primary family lacks a glyph.
        font.setFamilies([choice.ui, choice.cjk])
    app.setFont(font)
    return choice


def numeric_font(choice: Optional[FontChoice] = None, *, settings=None,
                 pixel_size: int = 13):
    """The font for fields that hold numbers.

    Defaults to the monospace family: digits are the same width, so a value
    does not shift as you type, and a font whose digits render oddly in the
    proportional family usually does not in the fixed-width one.
    """
    from PySide6.QtGui import QFont

    choice = choice or resolve_fonts()
    prefer = "mono"
    if settings is not None:
        prefer = getattr(settings.interface, "numeric_font", "mono")
        pixel_size = int(getattr(settings.interface, "font_size_px", pixel_size))
    family = choice.mono if prefer == "mono" else choice.ui
    font = QFont(family)
    font.setPixelSize(int(pixel_size))
    font.setStyleHint(
        QFont.StyleHint.TypeWriter if prefer == "mono" else QFont.StyleHint.SansSerif,
        QFont.StyleStrategy.PreferAntialias,
    )
    return font


def apply_numeric_font(widget, choice: Optional[FontChoice] = None, *,
                       settings=None, pixel_size: int = 13) -> None:
    """Give a spin box / line edit the numeric font and the C locale."""
    from PySide6.QtCore import QLocale

    widget.setFont(numeric_font(choice, settings=settings, pixel_size=pixel_size))
    try:
        widget.setLocale(QLocale(QLocale.Language.C))
    except AttributeError:
        pass


# --------------------------------------------------------------------------- #
# diagnosis
# --------------------------------------------------------------------------- #

#: What a spin box actually has to draw.  If any of this comes out wrong on a
#: machine, the specimen shows which family is responsible.
SPECIMEN_LINES: tuple[str, ...] = (
    "0123456789",
    "0.1160    -2.5    10  vertices    0.0 %",
    "L 38 / R 38    p < 0.001    x=-42.5",
    "cluster 皮层 左侧 顶点 阈值",
)


def render_specimen(
    path,
    *,
    settings=None,
    families: Optional[list[str]] = None,
    pixel_size: int = 13,
    include_widgets: bool = True,
):
    """Draw a font specimen to a PNG, so a display fault can be *seen*.

    Numbers that render as ``O.ıı ϗ OO`` cannot be debugged from a description:
    the string in memory is correct, so nothing in a log looks wrong.  This
    renders the same sample text in every candidate family, plus -- when a
    ``QApplication`` exists -- real spin boxes carrying the real stylesheet, so
    the broken family can be pointed at directly.

    Returns the path written.
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

    if QGuiApplication.instance() is None:
        raise RuntimeError(
            "render_specimen needs a Qt application; run it from "
            "'cifti-state check --fonts --sample <file.png>'"
        )

    path = Path(path)
    choice = resolve_fonts(force=True, settings=settings)

    candidates = families or list(
        dict.fromkeys(
            [choice.ui, choice.mono]
            + ([choice.cjk] if choice.cjk else [])
            + list(choice.bundled)
            + list(UI_FAMILIES)
            + list(MONO_FAMILIES)
        )
    )
    installed = {name.lower() for name in _available_families()[0]}
    candidates = [
        name for name in candidates
        if name.lower() in installed or name in (choice.ui, choice.mono)
    ]

    row_height = pixel_size * 2 + 10
    header = 92
    widget_block = 0
    grab = None
    if include_widgets:
        grab = _grab_numeric_widgets(pixel_size=pixel_size, settings=settings)
        if grab is not None:
            widget_block = grab.height() + 46

    width = 940
    height = header + widget_block + row_height * len(candidates) + 30
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#FFFFFF"))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    ink = QColor("#123338")
    muted = QColor("#5B8087")
    bad = QColor("#C4574F")

    title = QFont(choice.ui)
    title.setPixelSize(17)
    title.setBold(True)
    painter.setFont(title)
    painter.setPen(ink)
    painter.drawText(24, 32, "cifti_state font specimen")

    note = QFont(choice.ui)
    note.setPixelSize(11)
    painter.setFont(note)
    painter.setPen(muted)
    painter.drawText(
        24, 52,
        f"in use: interface {choice.ui} | monospace {choice.mono} | "
        f"CJK {choice.cjk or 'not needed'}   ({pixel_size}px)",
    )
    painter.drawText(
        24, 70,
        "Every line below should read exactly the same. Point at any row whose "
        "digits are wrong.",
    )

    y = header
    if grab is not None:
        painter.setPen(ink)
        painter.setFont(note)
        painter.drawText(24, y + 4, "the real controls, with the real stylesheet:")
        painter.drawImage(24, y + 14, grab)
        y += widget_block

    for family in candidates:
        sane = digits_are_sane(family)
        label = QFont(choice.ui)
        label.setPixelSize(10)
        painter.setFont(label)
        painter.setPen(bad if not sane else muted)
        painter.drawText(
            QRectF(24, y, 220, row_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            family + ("   (digits look wrong)" if not sane else ""),
        )

        sample = QFont(family)
        sample.setPixelSize(pixel_size)
        painter.setFont(sample)
        painter.setPen(ink)
        painter.drawText(
            QRectF(252, y, width - 276, row_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            SPECIMEN_LINES[1],
        )
        painter.setPen(QColor("#DDECEA"))
        painter.drawLine(24, int(y + row_height) - 1, width - 24,
                         int(y + row_height) - 1)
        y += row_height

    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path))
    log.info("font specimen written to %s (%d families)", path, len(candidates))
    return path


def _grab_numeric_widgets(*, pixel_size: int, settings=None):
    """A picture of real spin boxes under the real stylesheet, or ``None``."""
    try:
        from PySide6.QtWidgets import (
            QApplication,
            QDoubleSpinBox,
            QFormLayout,
            QLineEdit,
            QSpinBox,
            QWidget,
        )

        if QApplication.instance() is None:
            return None

        from .gui.theme import PALETTE, stylesheet

        panel = QWidget()
        panel.setStyleSheet(stylesheet(PALETTE, fonts=resolve_fonts(),
                                       font_size_px=pixel_size))
        form = QFormLayout(panel)
        form.setContentsMargins(12, 10, 12, 10)

        value = QDoubleSpinBox()
        value.setDecimals(4)
        value.setRange(-99.0, 99.0)
        value.setValue(0.1160)

        extent = QSpinBox()
        extent.setRange(0, 100000)
        extent.setValue(10)
        extent.setSuffix("  vertices")

        share = QDoubleSpinBox()
        share.setDecimals(1)
        share.setRange(0.0, 100.0)
        share.setValue(0.0)
        share.setSuffix(" %")

        plain = QLineEdit("0123456789   0.1160   -2.5")
        plain.setReadOnly(True)

        for widget in (value, extent, share, plain):
            apply_numeric_font(widget, settings=settings, pixel_size=pixel_size)

        form.addRow("Value", value)
        form.addRow("Extent", extent)
        form.addRow("Minimum share", share)
        form.addRow("Plain field", plain)
        panel.resize(520, panel.sizeHint().height())
        return panel.grab().toImage()
    except Exception as exc:  # pragma: no cover - best effort
        log.debug("could not grab the numeric widgets (%s)", exc)
        return None


def apply_numeric_fonts(root, *, settings=None, pixel_size: int = 13) -> int:
    """Give every spin box under *root* the numeric font. Returns how many.

    Done in one sweep from the window rather than field by field in each panel,
    so a spin box added later cannot be forgotten.
    """
    from PySide6.QtWidgets import QAbstractSpinBox

    choice = resolve_fonts()
    widgets = root.findChildren(QAbstractSpinBox)
    for widget in widgets:
        apply_numeric_font(widget, choice, settings=settings,
                           pixel_size=pixel_size)
    return len(widgets)


def apply_to_matplotlib(choice: Optional[FontChoice] = None) -> FontChoice:
    """Point matplotlib at the same families, and fix the minus sign."""
    import matplotlib

    choice = choice or resolve_fonts()
    stack = [name for name in choice.ui_stack if name != "sans-serif"]
    matplotlib.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": stack + list(matplotlib.rcParams["font.sans-serif"]),
            "font.monospace": [choice.mono] + list(matplotlib.rcParams["font.monospace"]),
            # U+2212 is missing from many CJK fonts; use ASCII '-' so negative
            # numbers never render as a box.
            "axes.unicode_minus": False,
            "axes.formatter.use_mathtext": False,
            "mathtext.default": "regular",
        }
    )
    return choice


def font_report() -> list[tuple[str, str]]:
    """Rows for ``cifti-state check --fonts``."""
    choice = resolve_fonts()
    rows = list(choice.summary())
    rows.append(("bundled font dir", str(bundled_font_dir())))
    rows.append(("stdout encoding", getattr(sys.stdout, "encoding", "?") or "?"))
    rows.append(("filesystem encoding", sys.getfilesystemencoding()))
    try:
        import matplotlib

        rows.append(
            ("matplotlib minus", str(matplotlib.rcParams["axes.unicode_minus"]))
        )
    except Exception:
        pass
    return rows

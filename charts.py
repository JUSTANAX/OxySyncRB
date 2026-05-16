from __future__ import annotations
import io
import os
from PIL import Image, ImageDraw, ImageFont

# ── Palette ───────────────────────────────────────────────────────────────────
_BG      = (13,  13,  30)
_HDR_BG  = (82,  36, 170)
_TH_BG   = (26,  24,  58)
_ROW     = [(20, 18, 46), (26, 24, 56)]
_BORDER  = (50,  46,  95)
_WHITE   = (232, 232, 255)
_DIM     = (125, 120, 165)
_GREEN   = (80,  215, 112)
_ZERO    = (75,  72,  112)
_DRAGON  = (255, 118,  55)
_UNICORN = (190,  95, 255)
_EGG     = (255, 210,  60)

# ── Font loading ──────────────────────────────────────────────────────────────
_REGULAR = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_BOLD = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_BOLD if bold else _REGULAR):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pet_color(name: str, is_egg: bool) -> tuple:
    n = name.lower()
    if is_egg:
        return _EGG
    if "dragon" in n:
        return _DRAGON
    if "unicorn" in n:
        return _UNICORN
    return _DIM


def _fmt_diff(val: int | None) -> str:
    if val is None:
        return "—"
    return f"+{val}" if val > 0 else str(val)


def _diff_color(val: int | None) -> tuple:
    if val is None or val == 0:
        return _ZERO
    return _GREEN


# ── Layout constants ──────────────────────────────────────────────────────────
_PAD      = 22
_HEADER_H = 68
_TH_H     = 42
_ROW_H    = 52
_FOOTER_H = 28

# (x_offset_from_content, width, header_label, align)
_COLS = [
    (0,   188, "Пет",   "left"),
    (188,  72, "Кол",   "right"),
    (260,  66, "+1ч",   "right"),
    (326,  74, "+12ч",  "right"),
    (400,  74, "+24ч",  "right"),
    (474,  66, "+3д",   "right"),
    (540,  74, "+7д",   "right"),
]
_CONTENT_W = 614
_WIDTH     = _CONTENT_W + 2 * _PAD  # 658

_PERIODS = ["1ч", "12ч", "24ч", "3д", "7д"]


# ── Main function ─────────────────────────────────────────────────────────────

def build_pets_image(pets: dict, period_diffs: dict) -> bytes:
    """
    pets: {pet_kind: {"name": str, "quantity": int, "is_egg": bool}}
    period_diffs: {"1ч": {pet_kind: int} | None, ...}

    Returns PNG bytes ready to pass to BufferedInputFile.
    """
    def _sort_key(item):
        kind, d = item
        n = d["name"].lower()
        if "dragon" in n and not d["is_egg"]:
            g = 0
        elif "dragon" in n and d["is_egg"]:
            g = 1
        elif "unicorn" in n and not d["is_egg"]:
            g = 2
        else:
            g = 3
        return (g, -d["quantity"])

    rows = sorted(pets.items(), key=_sort_key)
    n    = len(rows)
    if n == 0:
        n = 1  # still render the "no pets" skeleton

    height = _PAD + _HEADER_H + _TH_H + n * _ROW_H + _FOOTER_H + _PAD
    img  = Image.new("RGB", (_WIDTH, height), _BG)
    draw = ImageDraw.Draw(img)

    f_title  = _font(23, bold=True)
    f_th     = _font(15, bold=True)
    f_name   = _font(16)
    f_num    = _font(16, bold=True)
    f_foot   = _font(12)

    # ── Header bar ──────────────────────────────────────────────────────────
    draw.rectangle([0, 0, _WIDTH, _HEADER_H], fill=_HDR_BG)
    draw.text(
        (_PAD, _HEADER_H // 2),
        "OxySync  —  Pet Stats",
        font=f_title, fill=_WHITE, anchor="lm",
    )

    # ── Column headers ───────────────────────────────────────────────────────
    y_th = _HEADER_H
    draw.rectangle([0, y_th, _WIDTH, y_th + _TH_H], fill=_TH_BG)
    for col_x, col_w, label, align in _COLS:
        cx = _PAD + col_x
        cy = y_th + _TH_H // 2
        if align == "right":
            draw.text((cx + col_w - 4, cy), label, font=f_th, fill=_DIM, anchor="rm")
        else:
            draw.text((cx + 18, cy), label, font=f_th, fill=_DIM, anchor="lm")
    draw.line([0, y_th + _TH_H, _WIDTH, y_th + _TH_H], fill=_BORDER, width=1)

    # ── Data rows ────────────────────────────────────────────────────────────
    if not rows:
        y = _HEADER_H + _TH_H + _ROW_H // 2
        draw.text((_WIDTH // 2, y), "Нет данных о петах", font=f_name, fill=_DIM, anchor="mm")
    else:
        for i, (kind, data) in enumerate(rows):
            y_row  = _HEADER_H + _TH_H + i * _ROW_H
            row_bg = _ROW[i % 2]
            draw.rectangle([0, y_row, _WIDTH, y_row + _ROW_H], fill=row_bg)

            cy         = y_row + _ROW_H // 2
            pet_color  = _pet_color(data["name"], data["is_egg"])

            # Colored dot
            r = 5
            draw.ellipse(
                [_PAD + 3, cy - r, _PAD + 3 + r * 2, cy + r],
                fill=pet_color,
            )

            # Pet name (truncated)
            name = data["name"]
            if len(name) > 19:
                name = name[:18] + "…"
            draw.text((_PAD + 16, cy), name, font=f_name, fill=_WHITE, anchor="lm")

            # Quantity
            cx = _PAD + _COLS[1][0] + _COLS[1][1] - 4
            draw.text((cx, cy), str(data["quantity"]), font=f_num, fill=_WHITE, anchor="rm")

            # Period diffs
            for j, label in enumerate(_PERIODS):
                col_x, col_w = _COLS[2 + j][0], _COLS[2 + j][1]
                diffs = period_diffs.get(label)
                val   = None if diffs is None else diffs.get(kind, 0)
                draw.text(
                    (_PAD + col_x + col_w - 4, cy),
                    _fmt_diff(val),
                    font=f_name,
                    fill=_diff_color(val),
                    anchor="rm",
                )

            # Row separator
            if i < n - 1:
                draw.line(
                    [_PAD, y_row + _ROW_H - 1, _WIDTH - _PAD, y_row + _ROW_H - 1],
                    fill=_BORDER, width=1,
                )

    # ── Footer ───────────────────────────────────────────────────────────────
    y_foot = _HEADER_H + _TH_H + n * _ROW_H + _FOOTER_H // 2
    draw.text((_WIDTH // 2, y_foot + 4), "OxySync", font=f_foot, fill=_DIM, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()

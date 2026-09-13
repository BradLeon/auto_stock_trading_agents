"""Shared CJK font discovery for every Evidence chart.

The L1 review charts are written in Chinese.  A missing glyph silently renders as
a square box, so the chosen font is verified with the font's own charmap before
matplotlib is configured.  This module is the single implementation; older
renderers import from here so behaviour cannot drift between figures.
"""

from __future__ import annotations

from pathlib import Path

CANDIDATE_FONTS = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)
# Glyphs actually used across the L1 production and commercialization charts.
REQUIRED_GLYPHS = "采用率期间行业支出模型份额收入运行率最新历史变化开放人工预测说明公司月份来源估算披露"


def find_cjk_font() -> str | None:
    """Find an installed font that contains the Chinese glyphs used by charts."""
    try:
        from matplotlib import ft2font
    except Exception:
        return None
    for candidate in CANDIDATE_FONTS:
        if not Path(candidate).exists():
            continue
        try:
            cmap = ft2font.FT2Font(candidate).get_charmap()
        except Exception:
            continue
        if all(ord(char) in cmap for char in REQUIRED_GLYPHS):
            return candidate
    return None


def configure_font(plt) -> str | None:
    """Install a verified CJK font, avoiding macOS Arial square-glyph output."""
    path = find_cjk_font()
    if path:
        from matplotlib import font_manager

        font_manager.fontManager.addfont(path)
        name = font_manager.FontProperties(fname=path).get_name()
        plt.rcParams["font.family"] = [name, "DejaVu Sans"]
    else:
        # A readable English fallback is preferable to silently emitting boxes
        # on a minimal CI/container image.
        plt.rcParams["font.family"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return path


def glyph_report(text: str, *, font_path: str | None = None) -> dict[str, object]:
    """Report whether every character of ``text`` exists in the selected font."""
    path = font_path or find_cjk_font()
    if not path:
        return {"font_path": "", "checked_characters": len(text),
                "missing_glyphs": sorted(set(text)), "status": "font_not_found"}
    try:
        from matplotlib import ft2font

        cmap = ft2font.FT2Font(path).get_charmap()
    except Exception:
        return {"font_path": path, "checked_characters": len(text),
                "missing_glyphs": sorted(set(text)), "status": "glyph_check_unavailable"}
    missing = sorted({char for char in text if ord(char) not in cmap})
    return {"font_path": path, "checked_characters": len(set(text)),
            "missing_glyphs": missing,
            "status": "ok" if not missing else "missing_glyphs"}

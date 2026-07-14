"""The lifecycle report must always show the full gold-standard skeleton."""

from __future__ import annotations

from datetime import datetime, timezone

from ats.agents.pead import report as pead_report
from ats.schemas.pead import (
    PeadDossier,
    Scorecard,
    ScorecardDim,
    ScorecardLine,
)

SECTION_HEADERS = [
    "## 一、背景：核心叙事与估值逻辑",
    "## 二、市场预期（财报前确定）",
    "## 三、核心叙事—量化指标—业绩及前瞻（财报后填写）",
    "## 四、Surprise Scorecard（财报后填写）",
    "## 五、交易 Decision 框架（财报后填写）",
    "## 六、执行检查清单",
    "## 七、跨标的信号链",
]


def _minimal_dossier(**kw) -> PeadDossier:
    return PeadDossier(symbol="TSM", fiscal_label="Q FY2026",
                       updated_at=datetime.now(timezone.utc), **kw)


def test_minimal_dossier_renders_all_seven_sections():
    text = pead_report.render_dossier(_minimal_dossier())
    for header in SECTION_HEADERS:
        assert header in text, f"missing section: {header}"


def test_prep_phase_marks_post_earnings_sections_pending():
    text = pead_report.render_dossier(_minimal_dossier(phase="prep"))
    assert "⏳ 待财报后填写" in text
    # decision tree is static config knowledge — visible even at prep
    assert "Scorecard 总分" in text
    assert "正常入场" in text


def test_prep_scorecard_skeleton_shows_dims_and_weights():
    dims = [ScorecardDim(key="gm", weight=0.3, label="毛利率"),
            ScorecardDim(key="rev", weight=0.7, label="营收")]
    text = pead_report.render_dossier(
        _minimal_dossier(phase="prep", scorecard_dims=dims,
                         scorecard_weights={d.key: d.weight for d in dims}))
    assert "| 毛利率 | 30% | — | — | — |" in text
    assert "| 营收 | 70% | — | — | — |" in text


def test_score_phase_renders_scorecard_total_and_decision():
    sc = Scorecard(symbol="TSM", fiscal_label="Q FY2026",
                   as_of=datetime.now(timezone.utc),
                   lines=[ScorecardLine(dim_key="gm", label="毛利率", weight=0.3,
                                        score=1.5, weighted=0.45, note="超预期")],
                   total=0.45, threshold=1.0, band="未达门槛")
    text = pead_report.render_dossier(
        _minimal_dossier(phase="score", scorecard=sc, decision_summary="观望 | 建议: 观望"))
    assert "**+0.45**" in text
    assert "未达门槛" in text
    assert "观望 | 建议: 观望" in text
    # score phase fills in place of the pending marker for these sections
    assert "| 财报后 | Surprise Scorecard | ✅ |" in text

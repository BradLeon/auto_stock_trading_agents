"""Is the knowledge base load-bearing, or decoration?

Everything else about the KB is circumstantial. The audit shows the analyst CITES
criteria; it cannot show the criteria are what produced the score. A model that already
believes "optical beats copper" will write a rationale that cites the note either way,
and the citation proves nothing.

The only test that separates the two is to change the input and watch the output:

  ablate  remove §三 (the moat criteria) -> the moat scores should lose their spread
  poison  invert §三 (hardest evidence <-> softest) -> the ordering should follow it

If scores barely move, the note is not what is driving them, and every conclusion
resting on "the KB says so" is really resting on the model's prior.

Two properties make this safe to run and honest to read:

* **The production notes are never touched.** The perturbed copies live in a temp dir
  and are passed through `structure_notes`, which already takes arbitrary paths.
* **Both arms see identical everything else** — same quant rows, same chain evidence,
  same layer view. One data fetch, two model calls, so the diff is attributable to the
  note and nothing else.

Poison is destructive by design and its output is a lie about the industry. It is never
persisted: the run is in-memory and the basket is discarded.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ...data import industry

log = logging.getLogger("ats.agents.sector.kb_perturb")


@dataclass
class ArmScores:
    label: str
    scores: dict[str, tuple[float, float, str]]     # symbol -> (tenor, moat, rationale)


def _rewrite_criteria(text: str, fn) -> str:
    """Apply `fn` to each criteria section, leaving the rest of the note byte-identical.

    Sections are located by heading meaning (`industry.criteria_spans`), NOT by section
    number. The previous version split on the literal "## 三", which quietly made both
    perturbations no-ops on the two notes that outgrew the four-section template:
    `ablate` deleted 技术曲线 while writing a heading claiming the criteria were gone,
    and `poison` found no numbered items and returned the note unchanged — a control arm
    labelled 投毒, whose null result reads as "the criteria are not load-bearing".
    That is the one wrong answer this whole test exists to avoid.
    """
    spans = industry.criteria_spans(text)
    if not spans:
        return text
    out, prev = [], 0
    changed = False
    for a, b in spans:
        new = fn(text[a:b])
        changed = changed or new != text[a:b]
        out += [text[prev:a], new]
        prev = b
    out.append(text[prev:])
    return "".join(out) if changed else text


def ablate(text: str) -> str:
    """Drop the moat criteria entirely, leaving value chain + tech curve + pitfalls."""
    def strip(section: str) -> str:
        heading = section.split("\n", 1)[0]      # keep the note's own heading wording
        return heading + "\n\n（本节已移除——消融测试）\n\n"

    return _rewrite_criteria(text, strip)


def poison(text: str) -> str:
    """Reverse the numbered criteria, and flip the hard/soft labels with them.

    Reversing rather than rewriting keeps the vocabulary identical, so the arms differ
    in ORDER and nothing else. A model that is genuinely reading the ordering must
    reorder; a model reciting its prior will produce the same ranking either way — and
    that is the outcome worth knowing about.
    """
    def reverse(section: str) -> str:
        items = re.split(r"(?m)^(?=\s*\d+\.\s)", section)
        lead, numbered = items[0], [i for i in items[1:] if i.strip()]
        if len(numbered) < 2:
            return section
        flipped = []
        for n, item in enumerate(reversed(numbered), start=1):
            item = re.sub(r"(?m)^\s*\d+\.", f"{n}.", item, count=1)
            item = item.replace("**最硬**", "\x00").replace("**最软**", "**最硬**")
            item = item.replace("\x00", "**最软**")
            flipped.append(item)
        return lead + "".join(flipped)

    return _rewrite_criteria(text, reverse)


def control(text: str) -> str:
    """No change at all — the noise floor.

    Without it a delta is uninterpretable: the two arms are two separate model calls,
    so some movement happens for free, and "|Δmoat| = 0.2" means nothing until you know
    whether 0.2 also shows up when both arms read the identical file. Run this first on
    any layer whose perturbation result lands in the inconclusive band.
    """
    return text


MODES = {"ablate": ablate, "poison": poison, "control": control}


def _perturbed_notes(structure_notes: dict[str, str], mode: str, out_dir: Path) -> dict:
    """Write perturbed copies and return a structure_notes dict pointing at them."""
    from ...config import REPO_ROOT

    transform = MODES[mode]
    mapping: dict[str, str] = {}
    written: dict[str, str] = {}
    for name, rel in structure_notes.items():
        src = Path(rel) if Path(rel).is_absolute() else REPO_ROOT / rel
        if str(src) in written:
            mapping[name] = written[str(src)]
            continue
        dst = out_dir / src.name
        dst.write_text(transform(src.read_text(encoding="utf-8")), encoding="utf-8")
        written[str(src)] = str(dst)
        mapping[name] = str(dst)
    return mapping


def run(sector_name: str, layer_key: str, *, mode: str = "poison") -> tuple[ArmScores, ArmScores]:
    """Score the layer twice — real notes and perturbed notes — over identical inputs."""
    from ...chain import factor_evidence
    from ...config import canonical_entity, load_sector_config
    from ...memory import get_store
    from . import structure as struct_mod
    from .cross_section import _layer_view, fetch_factors, rank_cohort

    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    cfg = load_sector_config(sector_name)
    layer = next((ly for ly in cfg.layers if ly.key == layer_key), None)
    if layer is None:
        raise ValueError(f"layer {layer_key!r} not in sector {sector_name!r}")
    if not layer.structure_notes:
        raise ValueError(f"{layer_key} has no structure_notes — nothing to perturb")

    subgroups: dict[str, str] = {}
    cohort: list[str] = []
    for sym in [t.symbol for t in layer.tickers] + list(layer.cohort_extra):
        canon = canonical_entity(sym)
        if canon in cohort:
            continue
        cohort.append(canon)
        subgroups[canon] = next((t.subgroup for t in layer.tickers if t.symbol == sym),
                                "(peer)")
    rows = fetch_factors(cohort, subgroups)          # fetched ONCE, shared by both arms
    # The quant pass must run before scoring, exactly as `cross_section` does it: the
    # analyst's prompt prints `r.rank` for every name. Skipping it sent in a basket where
    # every row was rank 0, and the model — correctly — abstained on the whole cohort.
    # The control arm is what surfaced this: a layer that had just scored ASML 2.0 came
    # back all zeros, which no perturbation could explain.
    layer_cap = layer.weight_cap if layer.weight_cap is not None else 0.10
    extra = {canonical_entity(x) for x in layer.cohort_extra}
    for r in rows:
        r.sizable = r.symbol not in extra
    rank_cohort(rows, layer_cap=layer_cap)
    for r in rows:
        r.quant_rank = r.rank

    store = get_store()
    packs = factor_evidence.packs_for_layer(layer, store,
                                            cfg=cfg.review.get("corroboration", {}))
    evidence_ctx = factor_evidence.as_context(packs)
    view = _layer_view(sector_name, layer_key)

    def arm(label: str, notes: dict) -> ArmScores:
        scores, _ = struct_mod.assess(rows, notes, moat_context=evidence_ctx,
                                      layer_view=view)
        if not scores:
            # `assess` degrades to {} when the model call fails — by design, because an
            # overlay must never block a ranking. Here that default is dangerous: an
            # empty arm renders as "every score is 0", which under `poison` looks exactly
            # like a dramatic collapse and would be reported as strong evidence that the
            # KB is load-bearing. A failed call must abort the comparison, not become
            # its finding.
            raise RuntimeError(
                f"结构分析师在「{label}」臂上没有返回任何分数（调用失败或整体弃权）。"
                f"这一臂无法与另一臂比较——空结果和真的全 0 长得一模一样。请重跑。")
        return ArmScores(label=label, scores=scores)

    base = arm("原始", layer.structure_notes)
    with tempfile.TemporaryDirectory(prefix="ats-kbperturb-") as tmp:
        notes = _perturbed_notes(layer.structure_notes, mode, Path(tmp))
        other = arm({"ablate": "消融(删§三)", "poison": "投毒(倒序§三)",
                     "control": "对照(同一份笔记)"}[mode], notes)
    return base, other


def render(base: ArmScores, other: ArmScores, *, mode: str) -> str:
    """The comparison a person reads, plus a verdict on whether the note is load-bearing."""
    syms = sorted(set(base.scores) | set(other.scores))
    lines = [f"=== 知识库扰动测试（{mode}）===", "",
             f"{'sym':<12}{'ten(原)':>8}{'ten(扰)':>8}{'moat(原)':>9}{'moat(扰)':>9}"
             f"{'Δmoat':>8}", "-" * 56]
    # Only names the overlay actually scored in at least one arm carry information.
    # Averaging over the whole cohort let five abstentions (0.0 in both arms) dilute a
    # clean 1.2 collapse on the two names that had any signal down to 0.36 — turning
    # the strongest possible evidence of load-bearing into "inconclusive".
    deltas: list[float] = []
    informative: list[str] = []
    for s in syms:
        b = base.scores.get(s) or (0.0, 0.0, "")
        o = other.scores.get(s) or (0.0, 0.0, "")
        d = (o[1] or 0.0) - (b[1] or 0.0)
        if (b[1] or 0.0) or (o[1] or 0.0):
            deltas.append(abs(d))
            informative.append(s)
        lines.append(f"{s:<12}{b[0] or 0:>8.1f}{o[0] or 0:>8.1f}"
                     f"{b[1] or 0:>9.1f}{o[1] or 0:>9.1f}{d:>+8.1f}")

    def order(a: ArmScores) -> list[str]:
        # Ties are not an ordering. Sorting the whole cohort made an all-zero arm look
        # like it had "the same ranking", because a stable sort preserves insertion
        # order among equals.
        scored = {s: (v[1] or 0.0) for s, v in a.scores.items() if (v[1] or 0.0)}
        return [s for s, _ in sorted(scored.items(), key=lambda kv: -kv[1])]

    ob, oo = order(base), order(other)
    rank_moved = ob != oo
    mean_abs = sum(deltas) / len(deltas) if deltas else 0.0
    lines += ["",
              f"原始 moat 有分的：{' > '.join(ob) or '（无）'}",
              f"扰动 moat 有分的：{' > '.join(oo) or '（无）'}",
              f"有信息的标的 {len(informative)}/{len(syms)} · 平均 |Δmoat| = "
              f"{mean_abs:.2f} · 排序{'已改变' if rank_moved else '未变'}",
              ""]
    # Coarse on purpose: the question is "is the note driving this at all", a yes/no.
    #
    # And the inference is ONE-WAY. Movement proves the note is load-bearing. Absence of
    # movement proves nothing on its own, because two very different worlds produce it:
    # the note is being ignored, OR the model's own prior already agrees with the note
    # (ablate), OR the model noticed that the inverted criteria contradict their own
    # justification text and declined to follow them (poison). Reporting "no movement"
    # as 🔴 would be exactly the kind of confident-but-inverted verdict this whole
    # system exists to avoid.
    def abstained(a: ArmScores) -> bool:
        return not any((v[0] or 0.0) or (v[1] or 0.0) for v in a.scores.values())

    if abstained(base) != abstained(other):
        # One arm scored nobody at all. That is not a perturbation effect — it is the
        # analyst declining the whole cohort, and it renders as a clean sweep to zero,
        # i.e. as the most dramatic possible "the KB is load-bearing" result. Observed
        # on L3 on both sides (once the base arm, once the perturbed one) with identical
        # inputs, so it is run-to-run instability, not the note.
        which = base.label if abstained(base) else other.label
        lines += [f"🚫 **本次比较无效**：「{which}」臂对整个 cohort 全部给 0 分——"
                  f"这是整体弃权，不是扰动效果。",
                  "   两种情况在表里长得一模一样，所以这里不给结论。请重跑；"
                  "若反复出现，说明结构层的稳定性本身有问题，那是比 KB 更该先修的事。"]
        return "\n".join(lines)

    if mode == "control":
        lines.append(f"⚪ **对照臂**：两边读的是同一份笔记，所以这里的 {mean_abs:.2f} "
                     f"就是噪声底。低于它的扰动结果一律读作「没动」。")
        return "\n".join(lines)
    if not informative:
        lines.append("⚪ 两臂都没有给出任何非零 moat_pricing——本层的结构层整体弃权，"
                     "没有可比的东西。先让它有分可打（补命题/证人），再来跑扰动。")
    elif mean_abs >= 0.5 or rank_moved:
        lines.append("✅ **判据是 load-bearing**：改了笔记，分数跟着动。"
                     "这个方向的推断是可靠的。")
        # The 0.5 threshold is a default, not a measurement. Noise differs a lot by
        # layer — L6 came in at 0.05 and L3 at 0.33 on identical notes — so a result
        # just over 0.5 means very different things in the two.
        lines.append(f"   ⚠️ 但先跑一次 `--mode control` 拿到**本层**的噪声底再读这个数："
                     f"各层差很多，{mean_abs:.2f} 在噪声底 0.05 的层是强信号，"
                     f"在 0.33 的层只是勉强超出。")
        if oo == [] and ob:
            # Collapsing to zero rather than inverting is the honest response to
            # criteria that contradict their own justification: it declined to follow
            # them, AND it lost the basis for its positive scores. Both facts say the
            # note was what those scores rested on.
            lines.append("   注意形态：扰动后不是**倒过来**，而是**全部归零**——"
                         "模型没有照抄被倒置的判据，但也失去了给正分的依据。"
                         "这两件事都说明原来的分数确实建立在这份笔记上。")
    else:
        lines.append(f"⚪ **本测试没能给出结论**（平均 |Δmoat| = {mean_abs:.2f}）。"
                     "分数不动有三种可能，它区分不了：")
        lines += ["   1. 笔记确实没在起作用（模型在复述自己的先验）",
                  "   2. 模型的先验本来就与判据一致——那么删/倒判据当然不改结论",
                  ("   3. 倒序后的判据与它自己的论证文字自相矛盾，模型识别出来并拒绝了它"
                   if mode == "poison" else
                   "   3. §三 之外的内容（§一分工、§二曲线）已经足以推出同样的分数"),
                  "   → 换另一种 mode 交叉跑一次；两种都不动，第 1 种可能性才真正上升"]
    if mode == "poison":
        lines.append("（投毒结果不落库，本次运行是内存内的。）")
    return "\n".join(lines)

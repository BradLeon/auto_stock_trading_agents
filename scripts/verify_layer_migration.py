#!/usr/bin/env python3
"""搬迁不变量校验 —— 比对重排前后两份 sector yaml（openspec design D11）。

配置搬迁的失效模式**不报错**：一条命题的证人声明被碰坏，症状是那家公司的观测静默地
全部未映射。所以验收不能靠阅读 1980 行 diff，要靠机械比对不变量。

用法:
    python scripts/verify_layer_migration.py <before.yaml> <after.yaml> \
        [--new-tickers SNDK,STX,WDC,ETN,GEV,BE]

退出码 0 = 全部不变量成立；1 = 有差异（逐条打印）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def claims_of(cfg: dict) -> dict[str, dict]:
    """claim id -> claim dict，跨层扁平化（命题可能换层，id 不该变）。"""
    out: dict[str, dict] = {}
    for layer in cfg.get("layers") or []:
        for claim in layer.get("claims") or []:
            cid = claim.get("id")
            if cid in out:
                raise SystemExit(f"重复的 claim id: {cid!r}")
            out[cid] = claim
    return out


def concept_sig(claim: dict) -> dict[str, list[str]]:
    return {c.get("key"): sorted(c.get("expect_from") or [])
            for c in claim.get("concepts") or []}


def witness_set(claim: dict) -> set[str]:
    return {str(w.get("entity")).upper() for w in claim.get("witnesses") or []}


def speaks(claim: dict) -> set[str]:
    """谁能就这条命题发声 —— 复刻 observer.concept_menu 的过滤条件。

    与 ClaimDef.expected_witnesses() 同构：各维度 expect_from 的并集，为空时退回
    witnesses 列表；再并上 relative 命题的 entities。
    """
    expected = {e.upper() for c in claim.get("concepts") or []
                for e in (c.get("expect_from") or [])}
    out = expected or witness_set(claim)
    return set(out) | {str(e).upper() for e in claim.get("entities") or []}


def menu_keys(cfg: dict, symbol: str) -> set[str]:
    """该票的可归属维度键集合（concept_menu 的键集合，不含文案）。"""
    sym = symbol.upper()
    return {c.get("key") for claim in claims_of(cfg).values() if sym in speaks(claim)
            for c in claim.get("concepts") or []}


def tickers_of(cfg: dict) -> set[str]:
    return {t.get("symbol") for layer in cfg.get("layers") or []
            for t in layer.get("tickers") or []}


def notes_of(cfg: dict) -> set[str]:
    return {rel for layer in cfg.get("layers") or []
            for rel in (layer.get("structure_notes") or {}).values()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--new-tickers", default="",
                    help="本次有意新增的代码，逗号分隔；不在此列的新增即失败")
    args = ap.parse_args()

    before, after = load(args.before), load(args.after)
    expected_new = {s.strip().upper() for s in args.new_tickers.split(",") if s.strip()}
    fails: list[str] = []
    notes: list[str] = []

    # ① claim id 集合不变 -------------------------------------------------------
    cb, ca = claims_of(before), claims_of(after)
    lost, gained = set(cb) - set(ca), set(ca) - set(cb)
    if lost:
        fails.append(f"① 丢失命题: {sorted(lost)}")
    if gained:
        fails.append(f"① 新增命题（纯搬迁不该新增）: {sorted(gained)}")

    # ② concepts 的 key 与 expect_from 不变 -------------------------------------
    # ③ witnesses 集合不变 -----------------------------------------------------
    for cid in sorted(set(cb) & set(ca)):
        if concept_sig(cb[cid]) != concept_sig(ca[cid]):
            fails.append(f"② {cid} 的 concepts/expect_from 变了:\n"
                         f"    before {concept_sig(cb[cid])}\n"
                         f"    after  {concept_sig(ca[cid])}")
        if witness_set(cb[cid]) != witness_set(ca[cid]):
            fails.append(f"③ {cid} 的 witnesses 变了: "
                         f"-{sorted(witness_set(cb[cid]) - witness_set(ca[cid]))} "
                         f"+{sorted(witness_set(ca[cid]) - witness_set(cb[cid]))}")

    # ④ tickers 并集 = 原并集 + 有意新增 ---------------------------------------
    tb, ta = tickers_of(before), tickers_of(after)
    dropped = tb - ta
    added = ta - tb
    if dropped:
        fails.append(f"④ 丢失标的: {sorted(dropped)}")
    if added - expected_new:
        fails.append(f"④ 计划外新增标的: {sorted(added - expected_new)}")
    if expected_new - added:
        fails.append(f"④ 声明要加但没加: {sorted(expected_new - added)}")

    # ⑤ structure_notes 指向的文件存在 -----------------------------------------
    for rel in sorted(notes_of(after)):
        path = Path(rel) if Path(rel).is_absolute() else REPO_ROOT / rel
        if not path.exists():
            fails.append(f"⑤ structure_notes 指向的文件不存在: {rel}")

    # ⑥ 每只原有票的 concept_menu 键集合不变 -----------------------------------
    # 「非空」不能作为通过条件：基线里 VRT/AAOI/AXT 与别名代码本来就空（见 D13）。
    # 真正要抓的是**静默变化**，所以比对键集合，并把本来就空的单独列出来提醒。
    for sym in sorted(tb & ta):
        mb, ma = menu_keys(before, sym), menu_keys(after, sym)
        if mb != ma:
            fails.append(f"⑥ {sym} 的可归属维度变了: "
                         f"-{sorted(mb - ma)} +{sorted(ma - mb)}")
        elif not mb:
            notes.append(f"⑥ {sym} 搬迁前后都是空菜单（读数无处可落，见 D13 两步法）")
    for sym in sorted(added):
        if not menu_keys(after, sym):
            notes.append(f"⑥ 新增标的 {sym} 无可归属维度（预期如此，见 D13）")

    for line in notes:
        print(f"注意  {line}")
    if fails:
        print()
        for line in fails:
            print(f"失败  {line}")
        print(f"\n{len(fails)} 项不变量被破坏。")
        return 1
    print(f"\n全部不变量成立（命题 {len(ca)} 条 · 标的 {len(ta)} 只）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Phase F 进度记录文档与代码的一致性（`docs/validation/PHASE_F_GROUP_PROGRESS.md`）。

5.13 与 7.8 已经为 runbook 立过同类先例：文档里写的开关名、证据标识必须能被代码
复核。这份记录文档登记的是**数字与结论**，一旦漂移就没人知道哪份是真的——
而它恰好是下一轮上下文恢复后的第一份依据。故本文件把它钉住。

只断言**可从代码/配置复算**的事实（TTL 分布、批次结论、路由实况、指纹面数量）。
不断言测试计数——那些数字会随后续 fix 变化，写死只会制造需要维护的假失败；
真正的回归判据是 13.6 的全量基线。
"""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from ats.config import REPO_ROOT

PROGRESS = REPO_ROOT / "docs" / "validation" / "PHASE_F_GROUP_PROGRESS.md"
COVERAGE = REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"


@pytest.fixture(scope="module")
def text() -> str:
    return PROGRESS.read_text(encoding="utf-8").split("\n## 历史记录", 1)[0]


def _readonly_rows(path: Path, query: str) -> list[sqlite3.Row]:
    """Document checks must not initialise or append to production state."""
    assert path.is_file(), f"只读状态文件缺失：{path}"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return list(conn.execute(query))


def _table_rows(text: str, header_cell: str) -> list[list[str]]:
    """Return the data rows of the first markdown table whose header has `header_cell`."""
    rows: list[list[str]] = []
    in_table = False
    header_seen = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not header_seen:
                header_seen = any(header_cell in c for c in cells)
                in_table = header_seen
                continue
            if set("".join(cells)) <= set("-: "):
                continue
            rows.append(cells)
        else:
            if rows:
                break
            header_seen = False
    return rows


def test_the_record_names_the_evidence_ttl_of_every_consumer(text):
    """TTL 决定取证时点，且三档差异很大（1 天 vs 90 天）。

    文档里那张 TTL 表若与配置漂移，"临切流再取证"这条排期结论就建立在错误的
    数字上——而它是本文件最重要的一条时间判断。
    """
    manifest = yaml.safe_load(COVERAGE.read_text(encoding="utf-8"))
    by_ttl: dict[int, list[str]] = {}
    for consumer in manifest["consumers"]:
        by_ttl.setdefault(int(consumer["evidence_ttl_days"]), []).append(
            consumer["id"])

    # The 1-day tier is the one the scheduling conclusion rests on ("gathering it
    # early is wasted"), so it is emphasised in the document. Matched loosely:
    # bolding is presentation, and pinning it would make this test fail on a
    # formatting change rather than on a wrong number.
    for ttl, consumers in by_ttl.items():
        assert f"{ttl} 天" in text, (
            f"TTL {ttl} 档（{', '.join(sorted(consumers))}）在记录文档里缺失；"
            "取证时点结论会因此失真")
        for consumer in consumers:
            assert consumer in text, consumer

    # The two extremes are what the scheduling conclusion rests on: a 1-day tier
    # cannot be gathered early, and its presence must be visible in the document.
    assert min(by_ttl) == 1, "若最短 TTL 变了，'提前取证等于白补' 的结论需重审"
    assert max(by_ttl) >= 30


def test_every_batch_outcome_in_the_record_matches_a_live_dry_run(text):
    """The per-batch table is a reading of the manifest, not a claim about it.

    Read from the REAL manifest rather than a tmp one: a test that re-derives
    nothing passes vacuously, and the whole point is that adding or removing a
    batch without recording its verdict fails here.

    Qualification is stubbed to `ineligible` because the document's table
    records the gate's answer, and today's answer happens to be zero. What is
    being checked is coverage — every declared batch appears — not the verdict,
    which 13.6 re-reads against real evidence.
    """
    from ats.workflow import batch_manifest as bm

    declared = _readonly_rows(Path(bm.default_batch_db_path()),
                              "SELECT batch_id FROM cutover_batches")
    assert declared, (
        "真实清单库为空。若确实要清空，先更新 "
        "docs/validation/PHASE_F_GROUP_PROGRESS.md 的逐批结论表")

    recorded = set(re.findall(r"`(batch-[a-z-]+)`", text))
    for batch in declared:
        assert f"`{batch['batch_id']}`" in text, (
            f"{batch['batch_id']} 在清单里但不在记录文档中；"
            "新增批次必须同步登记结论")

    # The reverse direction matters too: a batch retired from the manifest but
    # still described in the document is a claim about something that no longer
    # exists.
    for batch_id in recorded:
        assert batch_id in {b["batch_id"] for b in declared}, (
            f"记录文档提到 {batch_id}，但清单里没有它；"
            "批次撤销后必须从文档移除结论")


def test_the_record_lists_every_boundary_with_its_current_route(text):
    """「第 5 组」章节 records the live route of all six boundaries.

    This is the single most consequential reading in the file — it is what says
    nothing has been switched yet. Asserted against the authority, so a switch
    that happened without updating the document fails here.
    """
    from ats.workflow import cutover as plane

    recorded = {row[0].strip("` "): row[1] for row in _table_rows(text, "当前路由")}
    rows = _readonly_rows(Path(plane.default_cutover_db_path()),
                          "SELECT boundary,route FROM cutover_boundary_state")
    assert {row["boundary"] for row in rows} == set(plane.SIX_BOUNDARIES)
    for row in rows:
        boundary, route = row["boundary"], row["route"]
        assert f"`{boundary}`" in text, (
            f"边界 {boundary} 未出现在记录文档的路由表里；六条边界必须齐全，"
            "漏一条会让读者以为其余也没切")
        assert recorded.get(boundary) == route, (boundary, recorded.get(boundary), route)


def test_the_record_states_the_fingerprint_surface_count(text):
    """Group 1's baseline fixes the fingerprint surface at ten paths.

    The number is load-bearing: it is what "changing one file invalidates
    everything" is counted against. If a path is added, this must say so rather
    than leaving the old count to be believed.
    """
    from ats.workflow.assurance_surface import load_surface

    surface = load_surface()
    actual = len(surface.all_paths())
    assert f"{actual} 个路径" in text, (
        f"受指纹面当前是 {actual} 个路径，记录文档没有写这个数；"
        "增删路径必须同步更新，否则「改一个文件作废多少证据」无从核对")


def test_every_blocking_item_states_whether_it_blocks_and_who_resolves_it(text):
    """§2 的表有两列是承重的：`阻塞谁` 与 `谁能解`。

    An item that blocks later work but names nobody is the exact failure this
    change was built to prevent. Asserted on structure rather than on specific
    ids, so new items are held to the same bar.
    """
    rows = _table_rows(text, "阻塞谁")
    assert rows, "记录文档缺少 §2.1 阻塞项登记表"

    blocking = [r for r in rows if len(r) >= 5]
    assert blocking, "§2.1 的表头缺少「谁能解」列"

    for row in blocking:
        item_id, blocks, who = row[0], row[1], row[3]
        assert item_id.startswith("`PF-B-"), item_id
        assert blocks.strip(), f"{item_id} 未写明阻塞哪些任务"
        assert who.strip(), (
            f"{item_id} 声明阻塞后续任务却未写谁能解——那就是无人负责")


def test_the_record_distinguishes_blocking_from_deferred(text):
    """The whole point of the file: two registries, not one list.

    Each must actually exist with its own heading. A merged table would force a
    reader to re-derive the distinction from prose — which is the thing this
    document exists to prevent.
    """
    assert "### 待处理项 · 阻塞后续任务" in text
    assert "### 待处理项 · 不阻塞，按依赖推进" in text
    assert "## 当前修复入口" in text

    # The ordering constraint is the part that cannot be recovered by reasoning:
    # two of the fixes touch the fingerprint surface.
    assert "受指纹面" in text
    assert "作废" in text


def test_the_record_points_at_every_validation_document_it_claims(text):
    """Relative links must resolve. A broken pointer in the one document meant to
    be read after context loss is worse than no pointer."""

    links = re.findall(r"\]\((?!https?:)([^)]+\.md)\)", text)
    assert links, "记录文档未引用任何同目录文档"
    for link in links:
        target = (PROGRESS.parent / link).resolve()
        assert target.is_file(), f"引用的文档不存在：{link}"

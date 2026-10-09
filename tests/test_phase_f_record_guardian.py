"""强制 Guardian：每组任务完成后必须在 `docs/validation/` 留下验证记录。

**为什么需要这个守卫**

`tasks.md` 只回答「做没做」。第 7 组全部勾选，但实测 5/10 合规；第 8 组全部
勾选，但 0 个批次 ready。这两组若只在 tasks.md 留一句「完成」，下一次会话读到的
就是「已完成」，缺口无处可寻——而上下文被压缩或清除后，那正是唯一的依据。

所以要求是：**勾选 ≠ 完成**。每一组勾选后，记录文档里必须有对应章节，写明
验证/演练结果、后续注意/待修复项，**以及为什么留着不修**。

**为什么从 tasks.md 推导而不是维护清单**

一份手写的「哪些组需要记录」清单，本身就是要同步的第二份状态——它会在有人
新增一组、勾了 checkbox 却忘了登记时静默失效，那正是要防的失效模式。这里改为
解析 tasks.md 的实际勾选状态，漏登记直接失败。

**判定粒度是「组」而不是「任务」**

任务号形如 `7.9`，组号形如 `## 7.`。文档章节用 `### 第 7 组 · 任务 7.1–7.10`，
两边的对应关系由标题里的组号建立，不依赖任务号出现在正文的任何位置。
"""

import re
from pathlib import Path

import pytest

from ats.config import REPO_ROOT

CHANGE = REPO_ROOT / "openspec" / "changes" / "implement-phase-f-shadow-run-and-cutover"
TASKS = CHANGE / "tasks.md"
RECORD = REPO_ROOT / "docs" / "validation" / "PHASE_F_GROUP_PROGRESS.md"

# 记录文档正文里对某组的交叉引用（"见 §1.5" 这类）不参与章节识别，故只认
# `### 第 N 组` 开头的标题。
GROUP_HEADING = re.compile(r"^###\s*第\s*(\d+)\s*组", re.MULTILINE)
TASKS_GROUP_HEADING = re.compile(r"^##\s*(\d+)\.\s", re.MULTILINE)
CHECKED_TASK = re.compile(r"^- \[x\]\s*(\d+(?:\.\d+)+)", re.MULTILINE)
HISTORICAL_REOPENED = "**记录状态：历史验证，任务已重开。**"

# 每组必须自证的三件事。
#
# 关键决定：**要求显式标记**，而不是靠词元在正文里猜位置。此前用「章节里出现过
# `原因`/`理由`/...」来判断，负向验证立刻暴露它无效——删掉整段「为何现在不修」
# 之后测试仍然全绿，因为别处（表格、缺陷描述）也有这些词。一个守不住东西的守卫
# 比没有守卫更糟：它让人以为该处有人在管。
#
# 因此本文件要求两行固定标记，出现在 follow-up 段落内：
#   `**后续注意/待修复项**` —— 段落起点
#   `**为何现在不修**`     —— 该段落内必须给出不修的理由
#
# `结论` 是例外，不要求星号：正文里它多以 `结论：**通过**` 出现，加星号反而
# 要求一种文档本来就不用的写法。
REQUIRED_PER_GROUP = {
    "结果": "结论：",
    "注意或待修复": "**后续注意/待修复项**",
    "原因": "**为何现在不修",
}


@pytest.fixture(scope="module")
def tasks_text() -> str:
    return TASKS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def record_text() -> str:
    return RECORD.read_text(encoding="utf-8")


# Group 1's record lives in its own frozen baseline document
# (`PHASE_F_GROUP1_BASELINE.md`, written before this file existed and kept
# unchanged since). Exempt here rather than duplicated: a second copy of a
# frozen record is a second thing to drift.
GROUPS_RECORDED_ELSEWHERE = frozenset({1})


def _groups_with_checked_tasks(tasks_text: str) -> dict[int, list[str]]:
    """Map group number -> the checked task ids inside it.

    Parsed from the document rather than a declared list, so a group added to
    tasks.md without a record section fails here instead of being silently
    exempt.
    """
    boundaries = [(m.start(), int(m.group(1)))
                  for m in TASKS_GROUP_HEADING.finditer(tasks_text)]
    checked = [(m.start(), m.group(1)) for m in CHECKED_TASK.finditer(tasks_text)]

    result: dict[int, list[str]] = {}
    for index, (start, group) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(tasks_text)
        inside = [tid for pos, tid in checked if start < pos < end]
        if inside:
            result[group] = inside
    return result


def _record_sections(record_text: str) -> dict[int, str]:
    """Map group number -> the body of its `### 第 N 组` section.

    The section runs to the **next `### ` heading**, not to the next heading of
    any level. A group's `####` subsections are part of its record — cutting them
    off would hide exactly the follow-ups and reasons this guardian asks for,
    since those are naturally written as subsections.
    """
    marks = list(GROUP_HEADING.finditer(record_text))
    sections: dict[int, str] = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(record_text)
        sections[int(mark.group(1))] = record_text[mark.end():end]
    return sections


def _validation_index(record_text: str) -> dict[str, tuple[str, str]]:
    """Explicit current-task evidence; historical mentions cannot fill the index."""
    heading = "## 当前勾选任务验证索引"
    if heading not in record_text:
        return {}
    section = record_text.split(heading, 1)[1].split("\n## ", 1)[0]
    result = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) != 3:
            continue
        if not re.fullmatch(r"\d+\.\d+(?:,\s*\d+\.\d+)*", cells[0]):
            continue
        for task in re.findall(r"\d+\.\d+", cells[0]):
            assert task not in result, f"任务 {task} 的当前验证索引重复"
            result[task] = (cells[1], cells[2])
    return result


def _unexplained_historical_groups(tasks_text: str, record_text: str) -> list[int]:
    checked = set(_groups_with_checked_tasks(tasks_text))
    return sorted(group for group, body in _record_sections(record_text).items()
                  if group not in checked and HISTORICAL_REOPENED not in body)


def _evidence_problems(tasks_text: str, record_text: str) -> list[str]:
    index = _validation_index(record_text)
    checked = set(CHECKED_TASK.findall(tasks_text))
    problems = [f"{task}: unrecorded" for task in sorted(checked - set(index))]
    problems += [f"{task}: unchecked" for task in sorted(set(index) - checked)]
    for task in sorted(checked & set(index)):
        evidence, result = index[task]
        links = re.findall(r"\]\(([^)]+)\)", evidence)
        if not links:
            problems.append(f"{task}: missing evidence link")
        if not result.strip():
            problems.append(f"{task}: missing scoped result")
        for link in links:
            target = (RECORD.parent / link.split("#", 1)[0]).resolve()
            if not target.is_file():
                problems.append(f"{task}: missing record {link}")
    return problems


def test_the_tasks_file_and_the_record_both_exist():
    """A guard that cannot fail is not a guard. Both paths are asserted so a
    rename or a move surfaces here rather than as an import error elsewhere."""
    assert TASKS.is_file(), TASKS
    assert RECORD.is_file(), RECORD


def test_at_least_one_group_is_checked(tasks_text):
    """If tasks.md parses to zero checked groups this file's assertions below are
    vacuous — so fail loudly rather than reporting green."""
    assert _groups_with_checked_tasks(tasks_text), (
        "tasks.md 未解析出任何已勾选的组；解析规则与文档结构不符，"
        "本文件的所有断言都会落空")


def test_every_checked_group_has_a_record_section(tasks_text, record_text):
    """The core requirement.

    A checked group with no section means its verification result and follow-ups
    live nowhere, which is the exact failure this change was assembled to
    prevent.
    """
    sections = _record_sections(record_text)
    missing = sorted(set(_groups_with_checked_tasks(tasks_text)) - set(sections))

    assert not missing, (
        f"这些组在 tasks.md 里已勾选，但 {RECORD.name} 没有对应章节："
        f"{missing}。勾选不等于完成——须补「验证/演练结果 + 后续注意/待修复项 + 原因」")


def test_each_recorded_group_states_its_result(tasks_text, record_text):
    """A section that lists work without stating an outcome is not a result.

    "Implemented X" and "verified X passes" are different claims; only the second
    one tells the next reader whether to trust it.
    """
    sections = _record_sections(record_text)
    checked = _groups_with_checked_tasks(tasks_text)

    silent = [g for g in checked if g in sections
              and REQUIRED_PER_GROUP["结果"] not in sections[g]]
    assert not silent, (
        f"这些组有记录章节但没有写验证结论（需含 "
        f"{REQUIRED_PER_GROUP['结果']}）：{sorted(silent)}")


def test_each_recorded_group_states_follow_ups_and_why_they_defer(tasks_text, record_text):
    """Three claims in one: something needs attention later, **and** the reason it
    is not being fixed now.

    The reason is the part that goes stale silently. Without it, a later reader
    either re-litigates a settled decision (wasting a session) or assumes the
    deferral is an oversight and "helpfully" fixes it — which for two of the
    registered items would invalidate recorded evidence.

    The reason must be an explicit marker **inside the follow-up paragraph**.
    Matching loose causal words anywhere in the section was tried first and does
    not work: deleting the entire "why we are not fixing it now" paragraph left
    the suite green, because the words also occur in tables and defect
    descriptions elsewhere in the section. A guard that cannot fail is worse than
    none — it suggests somebody is watching.
    """
    sections = _record_sections(record_text)
    checked = _groups_with_checked_tasks(tasks_text)

    missing_items, missing_reasons, hollow_reasons = [], [], []
    for group, body in sections.items():
        if group not in checked or group in GROUPS_RECORDED_ELSEWHERE:
            continue
        position = body.find(REQUIRED_PER_GROUP["注意或待修复"])
        if position == -1:
            missing_items.append(group)
            continue
        tail = body[position:]
        marker = REQUIRED_PER_GROUP["原因"]
        # Every occurrence, not just the first: a group may legitimately state
        # more than one deferral, and checking only the first let a hollow one
        # pass whenever a substantive one followed it.
        spots = [m.start() for m in re.finditer(re.escape(marker), tail)]
        if not spots:
            missing_reasons.append(group)
            continue
        # Deliberately coarse: only a marker with *nothing* after it is caught.
        #
        # A finer measure was tried and abandoned — segment boundaries proved
        # unreliable, because a deferral often spans several paragraphs and each
        # attempt to bound it left a case that passed hollow. Judging whether
        # prose is substantive is not a machine check; pretending otherwise
        # produces a test that looks rigorous and is not. What this catches is the
        # case that matters most: a group whose follow-ups were never explained.
        for spot in spots:
            after = tail[spot + len(marker):].lstrip("：:*").strip()
            if not after:
                hollow_reasons.append(group)
                break

    assert not missing_items, (
        f"这些组未记录「{REQUIRED_PER_GROUP['注意或待修复']}」："
        f"{sorted(missing_items)}。"
        "「全部顺利」也是一个合法结论，但必须写出来，不能靠留白表达")
    assert not missing_reasons, (
        f"这些组有 follow-up 段落却缺「{REQUIRED_PER_GROUP['原因']}」："
        f"{sorted(missing_reasons)}。不留理由的待办，下一轮要么被重新讨论一遍，"
        "要么被当成疏漏而擅自修掉")
    assert not hollow_reasons, (
        f"这些组有「{REQUIRED_PER_GROUP['原因']}」标记却后面无内容："
        f"{sorted(hollow_reasons)}。空标记满足存在性检查却什么也没记录")


def test_a_group_underway_cannot_be_marked_done_without_its_section(record_text):
    """The same rule seen from the other side: a section exists for a group whose
    tasks are all unchecked.

    Either direction of drift is a signal that the two documents stopped agreeing,
    and the reader cannot tell which one is telling the truth.
    """
    unstarted = _unexplained_historical_groups(
        TASKS.read_text(encoding="utf-8"), record_text)
    assert not unstarted, (
        f"这些组无当前勾选且未明确声明历史验证/任务重开：{unstarted}。"
        "历史记录可以保留，但不能冒充当前完成状态")


def test_each_current_checked_task_has_locatable_evidence_and_a_scoped_result(tasks_text, record_text):
    assert not _evidence_problems(tasks_text, record_text)


def test_reopened_group_requires_an_explicit_historical_marker():
    tasks = "## 2. module\n- [ ] 2.1 reopened\n"
    record = "### 第 2 组\n结论：历史通过\n"
    assert _unexplained_historical_groups(tasks, record) == [2]
    assert not _unexplained_historical_groups(tasks, record + HISTORICAL_REOPENED)


def test_historical_record_does_not_supply_a_newly_checked_tasks_evidence():
    tasks = "## 2. module\n- [x] 2.1 old\n- [x] 2.2 new\n"
    record = ("## 当前勾选任务验证索引\n"
              "| 2.1 | [record](PHASE_F_GROUP_PROGRESS.md) | old module |\n"
              "## history\n### 第 2 组\n2.2 曾提到，但未验证\n")
    assert _evidence_problems(tasks, record) == ["2.2: unrecorded"]


def test_an_empty_result_or_missing_link_is_visible_in_the_index():
    tasks = "## 2. module\n- [x] 2.1 checked\n"
    record = "## 当前勾选任务验证索引\n| 2.1 | no link |  |\n"
    assert _evidence_problems(tasks, record) == [
        "2.1: missing evidence link", "2.1: missing scoped result"]


def test_unchecked_tasks_and_deleted_records_are_rejected():
    record = ("## 当前勾选任务验证索引\n"
              "| 2.1 | [gone](PHASE_F_NONEXISTENT_RECORD.md) | passed |\n")
    assert _evidence_problems("- [x] 2.1 task", record) == [
        "2.1: missing record PHASE_F_NONEXISTENT_RECORD.md"]
    assert _evidence_problems("- [ ] 2.1 task", record) == ["2.1: unchecked"]


def test_the_record_states_the_current_progress_figures(tasks_text, record_text):
    """The document opens with a progress line. It is the first thing a reader
    after a context loss sees, and a stale count there is worse than none."""
    checked = re.findall(r"^- \[x\]", tasks_text, re.MULTILINE)
    unchecked = re.findall(r"^- \[ \]", tasks_text, re.MULTILINE)
    total = len(checked) + len(unchecked)

    head = record_text[:2000]
    assert re.search(rf"\*\*{len(checked)}/{total}\s*完成\*\*", head), (
        f"记录文档开头的进度数不是 {len(checked)}/{total}；"
        "上下文丢失后这是读者看到的第一句，必须与 tasks.md 一致")


def test_the_why_is_not_optional_for_registered_follow_ups(record_text):
    """§「待处理项」表里每一项都要带原因。

    The registered items are the ones that will survive into later sessions as
    bare table rows. A row saying what to do but not why it is not being done now
    is how an item gets "helpfully" fixed and takes recorded evidence with it.
    """
    start = record_text.find("### 待处理项 · 阻塞后续任务")
    assert start != -1, "缺少「阻塞后续任务」小节"
    end = record_text.find("\n### ", start + 10)
    blocking = record_text[start:end if end != -1 else len(record_text)]

    rows = [line for line in blocking.splitlines()
            if line.strip().startswith("|") and "`PF-" in line]
    assert rows, "阻塞项表里没有任何登记行"

    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        item_id = cells[0].strip("` ")
        # 4 columns minimum: id / 阻塞谁 / 根因 / 谁能解（表头另计）
        assert len(cells) >= 4, f"{item_id} 的登记行缺少「阻塞谁」或「谁能解」列"
        for index, cell in enumerate(cells[1:], start=1):
            assert cell, f"{item_id} 第 {index} 列为空"
        assert "原因" in blocking or "根因" in blocking or "因为" in blocking, (
            "阻塞项表没有说明这些项为何现在不能解决")

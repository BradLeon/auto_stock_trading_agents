"""Attribute every failure/error in a JUnit XML to an owner (task 13.6).

**Why a script and not a reading of the summary line.** 13.6 asks for a root
cause and a disposition for *each* remaining failure. A count is not an
attribution, and eyeballing 30 stack traces is how one of them gets quietly
forgiven. So the failures are grouped by test file and by signature, and each
group is compared against what is already registered as pre-existing.

**Two distinctions this preserves, because both have been conflated before:**

- **failure vs error.** A JUnit `<error>` is a test whose *setup* never
  completed — it never reached an assertion. Those are environment, not
  regression, and the summary counts them separately for exactly this reason.
- **pre-existing vs introduced.** A failure is only "pre-existing" if it was
  registered as such *before* this run, or reproduced on a clean worktree.
  Asserting it is pre-existing because the summary looked familiar is the exact
  mistake this script exists to prevent, so the registered set is passed in
  explicitly rather than inferred.
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path


def _ident(classname: str, name: str) -> tuple[str, str]:
    """Normalise a JUnit identity to `file.py::test` form.

    JUnit reports `classname` dotted (`tests.test_x`), while the registered list
    is written the way pytest prints it (`tests/test_x.py::test_y`). Comparing
    those two directly marks every pre-existing failure as NEW — which reads as
    "this change broke 45 tests" and is exactly the wrong conclusion to hand
    someone.
    """
    dotted = (classname or "").replace(".", "/")
    if dotted and not dotted.endswith(".py"):
        dotted = dotted + ".py"
    return dotted, name


def parse(junit: Path) -> tuple[list[dict], list[dict], dict]:
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))

    failures: list[dict] = []
    errors: list[dict] = []
    totals = Counter()

    for suite in suites:
        name = suite.get("name", "")
        for case in suite.iter("testcase"):
            path, test = _ident(case.get("classname", "") or name,
                                case.get("name", ""))
            for kind, bucket in (("failure", failures), ("error", errors)):
                node = case.find(kind)
                if node is None:
                    continue
                message = (node.get("message") or "").strip()
                text = (node.text or "").strip()
                bucket.append({
                    "id": f"{path}::{test}",
                    "file": path,
                    "message": message,
                    "last_frame": _last_frame(text),
                })
        for key, attr in (("tests", "tests"), ("errors", "errors"),
                          ("failures", "failures"), ("skipped", "skipped")):
            try:
                totals[key] += int(suite.get(attr) or 0)
            except ValueError:
                pass

    return failures, errors, dict(totals)


def _last_frame(text: str) -> str:
    """The deepest `File "...", line N` frame — where it actually broke.

    The message is often a shared helper's text, so the message alone groups
    unrelated failures together and hides that they have different owners.
    """
    frames = re.findall(r'File "([^"]+)", line (\d+)', text)
    return f"{frames[-1][0]}:{frames[-1][1]}" if frames else ""


def group(rows: list[dict]) -> dict[str, list[dict]]:
    """Group by file, then by message signature within the file.

    Two levels on purpose: the file is the owner, and the signature is what
    tells two failures in one file apart. Grouping by message alone would merge
    the same assertion text across files that have nothing in common.
    """
    by_file: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_file[row["file"]].append(row)
    return dict(sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def signature(message: str) -> str:
    """Collapse a message to its stable core.

    Numbers, ids and hex are stripped so the same defect reported for six
    consumers counts once — otherwise the count in the summary is a count of
    fixtures, not of problems.
    """
    text = message.split("\n")[0][:200]
    text = re.sub(r"0x[0-9a-f]+", "<hex>", text, flags=re.I)
    text = re.sub(r"\b[0-9a-f]{8,}\b", "<hash>", text, flags=re.I)
    text = re.sub(r"\d+", "<n>", text)
    return text.strip() or "(no message)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("junit")
    ap.add_argument("--registered", default="",
                    help="file with one registered pre-existing test id per line")
    args = ap.parse_args()

    failures, errors, totals = parse(Path(args.junit))
    registered = set()
    if args.registered and Path(args.registered).is_file():
        registered = {ln.strip() for ln in
                      Path(args.registered).read_text(encoding="utf-8").splitlines()
                      if ln.strip() and not ln.startswith("#")}

    print(f"== totals ==")
    print(f"tests={totals.get('tests', 0)} failures={len(failures)} "
          f"errors={len(errors)} skipped={totals.get('skipped', 0)}")

    print(f"\n== failures by file ({len(failures)} total) ==")
    for path, rows in group(failures).items():
        known = sum(1 for r in rows if r["id"] in registered)
        flag = f"  [registered pre-existing: {known}/{len(rows)}]" if known else ""
        print(f"\n{path}  ({len(rows)}){flag}")
        for sig, count in Counter(signature(r["message"]) for r in rows).most_common():
            print(f"   {count:>3}x {sig}")

    if errors:
        print(f"\n== errors by file ({len(errors)} total) ==")
        print("   ERROR = setup did not complete; the assertion never ran.")
        for path, rows in group(errors).items():
            print(f"\n{path}  ({len(rows)})")
            for sig, count in Counter(signature(r["message"]) for r in rows).most_common(3):
                print(f"   {count:>3}x {sig}")

    print("\n== every failing test id ==")
    for row in sorted(failures, key=lambda r: r["id"]):
        mark = "REGISTERED" if row["id"] in registered else "NEW"
        print(f"[{mark}] {row['id']}")
        print(f"          {signature(row['message'])[:150]}")
        if row["last_frame"]:
            print(f"          at {row['last_frame']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
#
# The single test entry point for this repository.
#
#   ./scripts/run_tests.sh                 # uv sync --all-extras, then run the suite
#   ./scripts/run_tests.sh --check         # install extras and probe that they import
#   ./scripts/run_tests.sh --batched 8     # split the suite into 8 batches (restricted envs)
#   ./scripts/run_tests.sh tests/test_risk.py -k survival   # any pytest args pass through
#
# Why one script: the baseline numbers cited in docs/ and in OpenSpec changes are only
# comparable when the command AND the dependency scope are fixed. Installing extras
# piecemeal (or relying on whatever a developer's machine happens to have) is what made
# earlier baselines unreproducible.
#
# Note on --basetemp: some execution environments refuse to create pytest's default
# temp root (/T/pytest-of-*) or quota its deletions. The basetemp therefore defaults to
# a directory inside the repo (gitignored tmp/), which those environments allow. Every
# cited measurement must record this; see docs/TEST_BASELINE.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="run"
EXTRA_ARGS=()
BATCH_SIZE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      MODE="check"; shift ;;
    --batched)
      MODE="batched"; shift
      if [[ $# -gt 0 && "$1" != -* ]]; then BATCH_SIZE="$1"; shift; fi ;;
    *)
      EXTRA_ARGS+=("$1"); shift ;;
  esac
done

SYNC_CMD=(uv sync --all-extras)
PROBE='from ats.runtime.optional_deps import ENVIRONMENT_PROBE_MODULES as M, missing_optional_modules as f
missing = f(M)
if missing:
    for module, group in missing:
        print(f"missing optional dependency: {module} (extras group: {group})")
    raise SystemExit(1)
print("environment ready: " + ", ".join(M))'

case "$MODE" in
  check)
    echo "== installing all extras =="
    "${SYNC_CMD[@]}"
    echo "== probing optional dependencies =="
    uv run python -c "$PROBE"
    ;;
  batched)
    echo "== installing all extras =="
    "${SYNC_CMD[@]}"
    exec uv run python scripts/run_tests_batched.py ${BATCH_SIZE:+--batch-size "$BATCH_SIZE"} "${EXTRA_ARGS[@]}"
    ;;
  run)
    echo "== installing all extras =="
    "${SYNC_CMD[@]}"
    BASETEMP="${ATS_PYTEST_BASETEMP:-$REPO_ROOT/tmp/pytest-basetemp/run-$(date +%Y%m%d-%H%M%S)}"
    mkdir -p "$(dirname "$BASETEMP")"
    echo "== running pytest (basetemp: $BASETEMP) =="
    exec uv run pytest -q --tb=no -rA -p no:cacheprovider --basetemp="$BASETEMP" "${EXTRA_ARGS[@]}"
    ;;
esac

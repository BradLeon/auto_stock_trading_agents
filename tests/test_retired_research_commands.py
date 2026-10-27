"""The commands that ran the research analysts by hand are retired; each says what replaces it (tasks 5.2–5.3)."""

import pytest

from ats.runtime import cli


RETIRED = [
    (["macro", "review"], "macro-review"),
    (["sector", "review"], "sector-review"),
    (["sector", "layer"], "layer-review"),
    (["technical", "review"], "technical-review"),
    (["analyst", "information"], "information-brief"),
    (["analyst", "fundamental"], "fundamental-routine"),
    (["pead", "prep", "NVDA"], "fundamental-routine"),
    (["pead", "score", "NVDA"], "ats workflow retry"),
    (["pead", "monitor", "NVDA"], "information-brief"),
    (["pead", "watch"], "information-brief"),
    (["pead", "research"], "information-brief"),
]


@pytest.mark.parametrize("argv,replacement", RETIRED)
def test_a_retired_command_names_its_replacement_and_runs_nothing(capsys, monkeypatch, argv, replacement):
    for name in ("run_macro_review", "run_sector_review", "run_layer_review", "run_technical_review",
                 "run_information_pass", "_run_analyst_fundamental", "run_pead", "run_pead_monitor",
                 "run_pead_watch", "run_pead_research"):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("a retired command ran the analyst"))
    code = cli.main(argv)
    out = capsys.readouterr().out
    assert code == 2 and "已退役" in out and replacement in out


def test_the_read_only_commands_are_not_retired():
    for argv in (["macro", "show"], ["technical", "show"], ["technical", "probe"], ["sector", "show"],
                 ["sector", "html"], ["sector", "probe"], ["sector", "kbperturb"], ["pead", "show"],
                 ["pead", "transcriptprobe"]):
        assert cli.RESEARCH_MANUAL_MOVED.get((argv[0], argv[1])) is None, argv

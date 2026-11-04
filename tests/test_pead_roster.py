from ats.config import is_pead_target, load_pead_global


def test_active_pead_roster_matches_current_portfolio_scope():
    targets = [str(symbol).upper() for symbol in load_pead_global()["targets"]]

    # 2026-11-04 (user): AMZN and MU moved up from observe; SNDK, AXTI, FORM added; TSLA and AAPL are followed
    # outside the AI-hardware chain.
    assert targets == [
        "GOOG", "MSFT", "AMZN", "NVDA", "AVGO", "MRVL", "SKHY", "MU", "SNDK",
        "TSM", "ASML", "LRCX", "LITE", "COHR", "AXTI", "FORM", "TSLA", "AAPL",
    ]
    assert all(is_pead_target(symbol) for symbol in targets)
    assert not any(is_pead_target(symbol) for symbol in ("CRDO", "VRT", "KLAC"))


def test_pead_roster_change_does_not_mutate_evidence_only_observe_scope():
    observe = {str(symbol).upper() for symbol in load_pead_global()["observe"]}

    assert observe == {
        "META", "ORCL", "005930.KS", "SPCX", "INTC", "AMAT", "AMD", "CRWV", "NBIS",
    }

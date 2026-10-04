from ats.config import is_pead_target, load_pead_global


def test_active_pead_roster_matches_current_portfolio_scope():
    targets = [str(symbol).upper() for symbol in load_pead_global()["targets"]]

    assert targets == [
        "GOOG", "NVDA", "SKHY", "TSM", "ASML", "COHR", "LRCX", "LITE",
        "AVGO", "MRVL", "MSFT",
    ]
    assert all(is_pead_target(symbol) for symbol in targets)
    assert not any(is_pead_target(symbol) for symbol in ("CRDO", "VRT", "KLAC"))


def test_pead_roster_change_does_not_mutate_evidence_only_observe_scope():
    observe = {str(symbol).upper() for symbol in load_pead_global()["observe"]}

    assert observe == {
        "MSFT", "AMZN", "META", "ORCL", "MU", "005930.KS", "SPCX", "INTC",
        "AMAT", "AMD", "CRWV", "NBIS",
    }

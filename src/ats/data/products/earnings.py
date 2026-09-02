"""Consumer-facing release-confirmation product for earnings workflows."""

from __future__ import annotations


def confirm_reported(symbol: str, print_) -> tuple[bool, str]:
    """Confirm a reported print without exposing a Provider-specific API.

    An actual EPS/revenue in the runtime calendar is already sufficient.  When the
    calendar lags, use the event-bound official disclosure pipeline as the fallback.
    """
    if print_.reported:
        return True, f"已公布（eps_actual={print_.eps_actual}）"
    from ..pipelines.unstructured.official import release_filed_on_or_after

    return release_filed_on_or_after(symbol, expected_date=print_.date)


__all__ = ["confirm_reported"]

"""Adapters for third-party statistical sources.

One module per agency, each exposing `fetch(lookback_months, **params) -> [SeriesPoint]`.
Deliberately not abstracted behind a common client: every agency's API differs in
authentication, pagination and field naming, and a config language that papered over
that would just be Python with worse error messages. The uniformity that matters lives
one level up — in the declaration (config/sources.yaml) and in the Observation these
produce (chain/sources.py).
"""

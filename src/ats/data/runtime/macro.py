"""Runtime macro inputs kept outside persistent structured observations."""


def fetch(*args, **kwargs):
    from ats.data import macro

    return macro.fetch(*args, **kwargs)


def fetch_series(*args, **kwargs):
    from ats.data import macro

    return macro.fetch_series(*args, **kwargs)


def series_spec(*args, **kwargs):
    from ats.data import macro

    return macro.series_spec(*args, **kwargs)

__all__ = ["fetch", "fetch_series", "series_spec"]

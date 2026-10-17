"""Consumer-level data-product health check for the research-read switch.

Replaces per-scope raw qualification events as the switch precondition
(Phase F simplification, 2026-10-15). It reads the data products directly and
read-only, never through a consumer's read gate: the gate itself is what the
switch changes, so checking through it before switching cannot work.

A check fails only on things that make the new route unsafe to rely on: an
unreadable product, malformed rows (no version, hash, source or lineage), or a
configured universe with nothing at all inside the lookback. A single ticker with
no fresh material is a warning, not a failure: quiet tickers are normal.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

DOCUMENT_LOOKBACK_DAYS = 7
# Same window as the document lookback: Information is not run daily yet, and a weekend must not
# roll Fundamental back for an upstream that is merely a few days old.
BRIEF_FRESH_DAYS = 7


def _universe() -> list[str]:
    from .runtime_reads import configured_entities

    return list(configured_entities("portfolio", "portfolio"))


def _latest_brief(entity: str) -> dict | None:
    """Newest published Information brief envelope, over a read-only connection.

    Deliberately not `get_store()`: opening the memory store applies pending additive
    schema, which would make a "read-only" health check write to the production file.
    """
    import json
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    from ..memory import _db_path

    path = Path(_db_path()).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_projection_envelopes'"
                            ).fetchone():
            return None
        row = conn.execute(
            "SELECT as_of, input_refs FROM task_projection_envelopes WHERE agent_role='information_brief' "
            "AND scope_kind='entity' AND scope_id=? AND status='published' ORDER BY created_at DESC LIMIT 1",
            (entity,)).fetchone()
    if row is None:
        return None
    try:
        refs = json.loads(row["input_refs"] or "[]")
    except json.JSONDecodeError:
        refs = []
    return {"as_of": row["as_of"], "input_refs": refs}


def _result(consumer: str, problems: list[str], warnings: list[str], detail: dict[str, Any]) -> dict[str, Any]:
    return {"consumer_id": consumer, "status": "ok" if not problems else "failed",
            "checked_at": datetime.now(UTC).isoformat(), "problems": problems,
            "warnings": warnings, "detail": detail}


def _information(now: datetime) -> dict[str, Any]:
    from ..data.products import get_platform_data_products
    from ..data.products.unstructured import admitted_documents

    problems, warnings, detail = [], [], {}
    since = (now - timedelta(days=DOCUMENT_LOOKBACK_DAYS)).isoformat()
    products = get_platform_data_products(readonly=True)
    try:
        total = 0
        for entity in _universe():
            try:
                rows = admitted_documents(entities=[entity], published_since=since, as_of=now,
                                          repository=products.unstructured, limit=200)
            except Exception as exc:  # noqa: BLE001 - unreadable product is the failure we look for
                problems.append(f"{entity}: admitted documents unreadable ({type(exc).__name__}: {exc})")
                continue
            malformed = [getattr(r, "document_id", "?") for r in rows
                         if not (getattr(r, "version_id", "") and getattr(r, "content_hash", "")
                                 and getattr(r, "source", ""))]
            if malformed:
                problems.append(f"{entity}: {len(malformed)} documents without version/hash/source")
            if not rows:
                warnings.append(f"{entity}: no admitted documents in the last {DOCUMENT_LOOKBACK_DAYS} days")
            detail[entity] = {"documents": len(rows)}
            total += len(rows)
        if total == 0:
            problems.append(f"no admitted documents for the whole universe in the last {DOCUMENT_LOOKBACK_DAYS} days")
    finally:
        products.structured.close()
    return _result("information", problems, warnings, detail)


def _fundamental(now: datetime) -> dict[str, Any]:
    from ..data.products import get_platform_data_products

    problems, warnings, detail = [], [], {}
    products = get_platform_data_products(readonly=True)
    try:
        financial_total, fresh_briefs = 0, 0
        for entity in _universe():
            row_detail: dict[str, Any] = {}
            try:
                snapshot = products.company_financial_snapshot(entity=entity, as_of=now)
                rows = snapshot.get("rows") or []
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{entity}: financial snapshot unreadable ({type(exc).__name__}: {exc})")
                rows = []
            unlinked = [r.get("observation_id") for r in rows[:5]
                        if not products.structured.lineage(r.get("observation_id"))]
            if unlinked:
                problems.append(f"{entity}: financial rows without lineage {unlinked}")
            if not rows:
                warnings.append(f"{entity}: no financial rows")
            row_detail["financial_rows"] = len(rows)
            financial_total += len(rows)
            brief = _latest_brief(entity)
            fresh = bool(brief and brief.get("input_refs") and brief.get("as_of")
                         and datetime.fromisoformat(brief["as_of"]) >= now - timedelta(days=BRIEF_FRESH_DAYS))
            row_detail["fresh_information_brief_with_lineage"] = fresh
            fresh_briefs += int(fresh)
            if not fresh:
                warnings.append(f"{entity}: no Information brief with lineage in the last {BRIEF_FRESH_DAYS} days")
            detail[entity] = row_detail
        if financial_total == 0:
            problems.append("no financial rows for the whole universe")
        # Fundamental reads Information's briefs (its only declared upstream). With none
        # at all from the new entry, the switched Fundamental would run on nothing new.
        if fresh_briefs == 0:
            problems.append(f"no Information brief with lineage in the last {BRIEF_FRESH_DAYS} days "
                            "(switch Information first and let it run)")
    finally:
        products.structured.close()
    return _result("fundamental", problems, warnings, detail)


PRICE_STALE_DAYS = 4          # a weekend or one holiday is not stale; older means the gateway is lagging
PRICE_RETRY_SECONDS = 60      # a transient provider hiccup must not roll Technical back


def _price_history(symbols):
    from ..data import sector_snapshot

    return sector_snapshot.fetch_close_history(symbols, period="1mo")


def _technical(now: datetime) -> dict[str, Any]:
    """Runtime price gateway (TARGET §4 MARKET_DATA, not persisted).

    Fails only when the whole universe has no usable recent bar, twice in a row within the check
    (one retry after `PRICE_RETRY_SECONDS`); a single symbol without a bar is a warning.
    """
    import time

    symbols = _universe()
    problems, warnings, detail = [], [], {}
    for attempt in (1, 2):
        try:
            rows = _price_history(symbols)
        except Exception as exc:  # noqa: BLE001 - an unreadable gateway is what we look for
            rows, error = {}, f"{type(exc).__name__}: {exc}"
        else:
            error = ""
        usable = {}
        for symbol in symbols:
            row = rows.get(symbol)
            bar = getattr(row, "bar_as_of", None) if row is not None else None
            ok = row is not None and getattr(row, "status", "") == "succeeded" and bar is not None
            age = (now.date() - (bar.date() if hasattr(bar, "date") else bar)).days if ok else None
            detail[symbol] = {"bar_as_of": str(bar) if bar else "", "source": getattr(row, "source", "") if row else "",
                              "age_days": age}
            if ok and age is not None and age <= PRICE_STALE_DAYS:
                usable[symbol] = age
        if usable:
            break
        if attempt == 1 and PRICE_RETRY_SECONDS:
            time.sleep(PRICE_RETRY_SECONDS)
    if not usable:
        problems.append("no symbol in the universe has a recent price bar"
                        + (f" ({error})" if error else "") + " after a retry")
    for symbol in symbols:
        if symbol not in usable:
            warnings.append(f"{symbol}: no usable bar within {PRICE_STALE_DAYS} days")
    return _result("technical", problems, warnings, detail)


# Macro (2026-10-17): the review reads four stored inputs. Only the first two can make it impossible.
MACRO_SERIES_MIN_COVERAGE = 0.5   # share of the defined indicator series that must be stored
MACRO_SERIES_COLLECTION_DAYS = 5  # newest stored print older than this: the daily collection looks stopped (warning)
MACRO_NEWS_NEWEST_DAYS = 7        # newest admitted theme news older than this (warning)
MACRO_REGIONAL_STALE_DAYS = 45    # days after the END of the latest monthly period; one missed release (warning)
MACRO_FACTSET_STALE_DAYS = 14     # FactSet index report is weekly; two missed reports (warning)


def _macro(now: datetime) -> dict[str, Any]:
    """Stored indicator series, admitted theme news, regional exports and the FactSet index packet.

    Fails only when the review could not be done: the series product or the news product is unreadable,
    the stored series are less than half of those defined or the whole daily feed has gone stale, a news
    document lacks version/hash/source, or no theme has any admitted news at all (the collection is then
    demonstrably broken). Everything per-input -- one missing or stale series, a theme without news,
    old regional or FactSet material, an unreadable regional/FactSet product (both additive in the
    review) -- is a warning.
    """
    import calendar

    from ..data.products import get_platform_data_products, macro_inputs

    problems, warnings, detail = [], [], {}
    products = get_platform_data_products(readonly=True)
    try:
        state = macro_inputs.stored_input_state(products, as_of=now)
    finally:
        products.structured.close()

    series_state = state["series"]
    if series_state["error"]:
        problems.append(f"indicator series unreadable ({series_state['error']})")
    else:
        data = series_state["value"]
        from ..data.products.macro_series import STALE_DAYS
        from ..data.sources.macro_series import all_series

        specs = all_series()
        coverage = len(data.series) / len(specs) if specs else 0.0
        daily = [key for key, spec in specs.items() if spec[4] == "daily"]
        stale_keys = {key for key, _last, _age in data.stale}
        live_daily = [key for key in daily if key in data.series and key not in stale_keys]
        newest = max(data.known_at.values(), default="")
        detail["series"] = {"stored": len(data.series), "defined": len(specs), "stale": len(stale_keys),
                            "live_daily": len(live_daily), "newest_known_at": newest}
        if not data.series:
            problems.append("no indicator series stored (macro_series_daily has not collected)")
        else:
            if coverage < MACRO_SERIES_MIN_COVERAGE:
                problems.append(f"only {len(data.series)}/{len(specs)} indicator series stored")
            if daily and not live_daily:
                problems.append(f"no daily indicator has a print within {STALE_DAYS['daily']} days "
                                "(daily collection stopped)")
            if data.gaps:
                warnings.append("indicator series not stored: " + ", ".join(data.gaps))
            for key, last, age in data.stale:
                warnings.append(f"indicator {key} stale: last period {last} ({age} days old)")
            if newest and (now - datetime.fromisoformat(newest)).days > MACRO_SERIES_COLLECTION_DAYS:
                warnings.append(f"newest stored indicator print was collected {newest}; "
                                "macro_series_daily may have stopped")

    news_state = state["news"]
    if news_state["error"]:
        problems.append(f"admitted macro news unreadable ({news_state['error']})")
    else:
        themes, days = news_state["value"]["themes"], news_state["value"]["days"]
        malformed = [getattr(d, "document_id", "?") for docs in themes.values() for d in docs
                     if not (getattr(d, "version_id", "") and getattr(d, "content_hash", "")
                             and getattr(d, "source", ""))]
        if malformed:
            problems.append(f"{len(malformed)} macro news documents without version/hash/source")
        total = sum(len(docs) for docs in themes.values())
        detail["news"] = {theme: len(docs) for theme, docs in themes.items()}
        if total == 0:
            problems.append(f"no admitted macro news for any theme in the last {days} days "
                            "(macro_theme_news_daily has not collected)")
        else:
            for theme, docs in themes.items():
                if not docs:
                    warnings.append(f"theme {theme}: no admitted news in the last {days} days")
            newest = max((d.published_at for docs in themes.values() for d in docs), default="")
            if newest and (now - datetime.fromisoformat(newest.replace("Z", "+00:00"))).days > MACRO_NEWS_NEWEST_DAYS:
                warnings.append(f"newest admitted macro news was published {newest[:10]}")

    if state["regional"]["error"]:
        warnings.append(f"regional exports unreadable ({state['regional']['error']})")
    else:
        for point in state["regional"]["value"]:
            detail.setdefault("regional", {})[point["id"]] = point["period"] or "none"
            if not point["period"]:
                warnings.append(f"regional {point['id']}: no stored data")
                continue
            year, month = (int(part) for part in point["period"].split("-")[:2])
            age = (now.date() - date(year, month, calendar.monthrange(year, month)[1])).days
            if age > MACRO_REGIONAL_STALE_DAYS:
                warnings.append(f"regional {point['id']}: latest period {point['period']} is {age} days "
                                f"past its end (> {MACRO_REGIONAL_STALE_DAYS}); a monthly release was missed")

    if state["factset"]["error"]:
        warnings.append(f"FactSet index packet unreadable ({state['factset']['error']})")
    else:
        packet = state["factset"]["value"]
        detail["factset"] = {"report_date": packet["report_date"], "version_id": packet["version_id"],
                             "age_days": packet["age_days"]}
        if not packet["version_id"]:
            warnings.append(f"no released FactSet index report ({packet['state']})")
        elif packet["age_days"] is not None and packet["age_days"] > MACRO_FACTSET_STALE_DAYS:
            warnings.append(f"FactSet index report {packet['report_date']} is {packet['age_days']} days old "
                            f"(> {MACRO_FACTSET_STALE_DAYS})")
    return _result("macro", problems, warnings, detail)


CHECKS = {"information": _information, "fundamental": _fundamental, "technical": _technical, "macro": _macro}


def check(consumer_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Read-only health of the data products one research consumer depends on."""
    if consumer_id not in CHECKS:
        return _result(consumer_id, [f"no health check defined for {consumer_id}; it cannot switch yet"], [], {})
    return CHECKS[consumer_id](now or datetime.now(UTC))


# --------------------------------------------------------------------------------------------------------
# Layer and Sector (2026-10-17). Appended below `check` on purpose and registered into CHECKS, so this block
# does not touch the Macro/Technical code above. What each review reads is described once, in
# `layer_sector_inputs`, which the Dispatcher's data-vintage reference uses as well.
# --------------------------------------------------------------------------------------------------------
LAYER_EVIDENCE_STALE_DAYS = 120   # extraction is quarterly (earnings calls): a layer with nothing newer than one
#                                   reporting cycle plus a month looks like extraction stopped (warning)
SECTOR_CONSENSUS_STALE_DAYS = 14  # the consensus refresh is daily for the PEAD targets and `consensus.MAX_AGE_DAYS`
#                                   is 14: older than that is a warning for the names concerned
LAYER_PROJECTION_STALE_DAYS = 7   # = FRESHNESS_SECONDS of layer-review; older ones are rebuilt by the Dispatcher


def _unstructured_repository():
    from ..data.stores.unstructured import get_platform_unstructured_repository

    return get_platform_unstructured_repository()   # read-only unless asked otherwise


def _layer(now: datetime) -> dict[str, Any]:
    """Neutral evidence observations of every claim witness and the governed knowledge notes, per layer.

    The Dispatcher adapter gives the Layer review exactly these two inputs (no cross-section basket). It fails
    only when the review could not be done: the config or a product is unreadable, an observation lacks its
    identity or document, nothing at all was observed for the whole sector, or none of the layers' knowledge
    notes is published. A thin layer is a warning because the review's own code handles it: a layer without
    claims, or with claims that nothing answers, is published as `steady` with confidence capped at 0.3 and
    says so in its text.
    """
    from . import layer_sector_inputs as inp

    problems, warnings, detail = [], [], {}
    total, notes_wanted, notes_ok = 0, 0, 0
    for sector in inp.configured_sectors():
        try:
            cfg = inp.load_sector(sector)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{sector}: sector config unreadable ({type(exc).__name__}: {exc})")
            continue
        if not cfg.layers:
            problems.append(f"{sector}: no layers configured")
            continue
        repository = _unstructured_repository()
        try:
            for layer in cfg.layers:
                row: dict[str, Any] = {"claims": len(layer.claims)}
                detail[layer.key] = row
                try:
                    evidence = inp.layer_evidence(repository, layer)
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{layer.key}: evidence observations unreadable ({type(exc).__name__}: {exc})")
                    continue
                total += evidence["observations"]
                row.update(witnesses=len(evidence["by_entity"]), observations=evidence["observations"],
                           newest_observed_at=evidence["newest"], silent_witnesses=evidence["silent"])
                if evidence["malformed"]:
                    problems.append(f"{layer.key}: {len(evidence['malformed'])} observations without "
                                    "id/entity/time/document")
                if not layer.claims:
                    warnings.append(f"{layer.key}: no claims configured (config gap): the review is blind, "
                                    "status steady, confidence capped at 0.3")
                elif evidence["observations"] == 0:
                    warnings.append(f"{layer.key}: no observations for any of its {len(evidence['by_entity'])} "
                                    "witnesses: blind round, status steady, confidence capped at 0.3")
                else:
                    age = inp.age_days(evidence["newest"], now)
                    if age is not None and age > LAYER_EVIDENCE_STALE_DAYS:
                        warnings.append(f"{layer.key}: newest observation {evidence['newest'][:10]} is "
                                        f"{age:.0f} days old (> {LAYER_EVIDENCE_STALE_DAYS})")
                    if evidence["silent"]:
                        warnings.append(f"{layer.key}: no observations for witnesses "
                                        + ", ".join(evidence["silent"]))
                paths = inp.note_paths(layer)
                try:
                    packet = inp.knowledge_notes(paths)
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{layer.key}: knowledge notes unreadable ({type(exc).__name__}: {exc})")
                    continue
                notes_wanted += len(paths)
                notes_ok += len(packet["notes"])
                row["knowledge_notes"] = {"published": len(packet["notes"]), "missing": packet["missing"]}
                for item in packet["missing"]:
                    warnings.append(f"{layer.key}: knowledge note {item['path']} {item['reason']}")
        finally:
            repository.close()
    if detail and total == 0 and not problems:
        problems.append("no evidence observations for any witness of any layer (chain_evidence has never run)")
    if notes_wanted and notes_ok == 0:
        problems.append("none of the layers' knowledge notes is published (ai_hardware_knowledge_corpus)")
    return _result("layer", problems, warnings, detail)


def _sector(now: datetime) -> dict[str, Any]:
    """The eight LayerAnalysis projections plus the cross-section and FactSet inputs of the rotation pass.

    The Dispatcher plan completes the Layer dependency itself, but the entry that stays runnable while the
    read route is on target and the schedule owner is still legacy (`ats sector review`) reads the latest
    projection of every configured layer and refuses to run on fewer than all of them, so a missing or
    unusable one is a failure ("run Layer first"). Governed financials or consensus with nothing at all for
    the whole cohort universe are failures too (the platform feed is broken, no provider fallback exists);
    thin coverage is a warning because the cross-section then scores a missing name cohort-neutral. FactSet
    unavailable is a warning: the rotation prompt says so explicitly. Runtime prices and market data are
    fetched when the review runs and are deliberately not probed here.
    """
    from . import layer_sector_inputs as inp
    from ..data import factset
    from ..data.products import get_platform_data_products

    problems, warnings, detail = [], [], {}
    for sector in inp.configured_sectors():
        try:
            cfg = inp.load_sector(sector)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{sector}: sector config unreadable ({type(exc).__name__}: {exc})")
            continue
        if not cfg.layers:
            problems.append(f"{sector}: no layers configured")
            continue

        # 1. The dependency: exactly one usable LayerAnalysis per configured layer.
        try:
            stored = inp.latest_layer_projections()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"LayerAnalysis projections unreadable ({type(exc).__name__}: {exc})")
            stored = {}
        absent, unusable, old = [], [], []
        projections = {}
        for layer in cfg.layers:
            row = stored.get(layer.key)
            if row is None:
                absent.append(layer.key)
                continue
            age = inp.age_days(row.get("created_at") or row.get("as_of"), now)
            projections[layer.key] = {"projection_id": row["projection_id"], "as_of": row["as_of"],
                                      "age_days": None if age is None else round(age, 2),
                                      "usable": row["usable"], "problem": row["problem"]}
            if not row["usable"]:
                unusable.append(f"{layer.key} ({row['problem']})")
            elif age is not None and age > LAYER_PROJECTION_STALE_DAYS:
                old.append(layer.key)
        detail["layer_projections"] = projections
        if absent or unusable:
            problems.append(f"{sector}: LayerAnalysis not usable for {len(absent) + len(unusable)} of "
                            f"{len(cfg.layers)} layers (missing: {', '.join(absent) or '-'}; unusable: "
                            f"{', '.join(unusable) or '-'}); run the Layer review first")
        if old:
            warnings.append(f"LayerAnalysis older than {LAYER_PROJECTION_STALE_DAYS} days for "
                            + ", ".join(old) + " (the Dispatcher rebuilds them)")

        # 2. Governed constituent financials and platform consensus for the cross-section cohort.
        symbols = {layer.key: inp.cohort(layer) for layer in cfg.layers}
        universe = sorted({s for group in symbols.values() for s in group})
        products = None
        try:
            products = get_platform_data_products(readonly=True)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"data products unreadable ({type(exc).__name__}: {exc})")
        if products is not None:
            try:
                covered, with_consensus, stale = {}, {}, []
                for symbol in universe:
                    try:
                        state = inp.financial_state(products, symbol, as_of=now)
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"{symbol}: governed financials unreadable ({type(exc).__name__}: {exc})")
                        continue
                    covered[symbol] = state["covered"]
                    try:
                        known = products.consensus_snapshot(entity=symbol, as_of=now).get("known_at")
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"{symbol}: consensus snapshot unreadable ({type(exc).__name__}: {exc})")
                        continue
                    with_consensus[symbol] = known
                    age = inp.age_days(known, now) if known else None
                    if known and age is not None and age > SECTOR_CONSENSUS_STALE_DAYS:
                        stale.append(symbol)
                detail["financials"] = {"covered": sorted(s for s, ok in covered.items() if ok),
                                        "uncovered": sorted(s for s, ok in covered.items() if not ok),
                                        "universe": len(universe)}
                detail["consensus"] = {"present": sorted(s for s, k in with_consensus.items() if k),
                                       "absent": sorted(s for s, k in with_consensus.items() if not k),
                                       "stale": sorted(stale)}
                if covered and not any(covered.values()):
                    problems.append(f"no governed financials for any of the {len(universe)} cohort names")
                if with_consensus and not any(with_consensus.values()):
                    problems.append(f"no platform consensus for any of the {len(universe)} cohort names")
                for layer in cfg.layers:
                    missing = [s for s in symbols[layer.key] if covered.get(s) is False]
                    if missing and len(missing) < len(symbols[layer.key]):
                        warnings.append(f"{layer.key}: no governed financials for {len(missing)} of "
                                        f"{len(symbols[layer.key])} names ({', '.join(missing)}): scored cohort-neutral")
                    elif missing:
                        warnings.append(f"{layer.key}: no governed financials for any of its "
                                        f"{len(missing)} names: the cross-section is empty")
                if stale:
                    warnings.append(f"consensus older than {SECTOR_CONSENSUS_STALE_DAYS} days for "
                                    f"{len(stale)} names ({', '.join(sorted(stale))})")
                absent_consensus = [s for s, k in with_consensus.items() if not k]
                if absent_consensus and any(with_consensus.values()):
                    warnings.append(f"no platform consensus for {len(absent_consensus)} names "
                                    f"({', '.join(sorted(absent_consensus))})")

                # 3. The FactSet sector packet of the cross-layer rotation (a local snapshot).
                try:
                    material = factset.fetch_sector_material(products=products)
                except Exception as exc:  # noqa: BLE001
                    material = {"state": "unreadable", "reason": f"{type(exc).__name__}: {exc}"}
                detail["factset"] = {key: material.get(key) for key in
                                     ("state", "version_id", "report_date", "freshness", "reason")}
                if not material.get("text"):
                    warnings.append("FactSet sector packet unavailable for the rotation pass: "
                                    + str(material.get("reason") or material.get("state")))
            finally:
                products.structured.close()
                products.unstructured.close()
    detail["not_probed"] = "runtime prices, market cap, beta and forward PE are fetched when the review runs"
    return _result("sector", problems, warnings, detail)


CHECKS["layer"] = _layer
CHECKS["sector"] = _sector

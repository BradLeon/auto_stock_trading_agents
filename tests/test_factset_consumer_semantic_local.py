from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from ats.data.factset import render_macro_analysis_packet
from ats.data import factset
from ats.data.products import earnings_insight as product
from ats.data.products.base import DataProducts
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.release import ReleaseManager, pinned_product_version
from ats.data.rollout_modes import read_mode, source_mode
import pytest


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def test_macro_packet_keeps_all_released_periods_and_bounded_source_mentions(monkeypatch):
    observations = {
        period: {'earnings.bottom_up_eps': product.EarningsInsightObservation(
            observation_id=f'obs-{period}', entity_id='SP500',
            metric_id='earnings.bottom_up_eps', period=period,
            period_basis='target_quarter', estimate_state='estimated',
            value=value, unit='currency_per_share', known_at=NOW)}
        for period, value in [('2026Q2', 72.0), ('2026Q3', 90.03)]
    }
    snapshot = product.EarningsInsightSnapshot(
        report=product.EarningsInsightReport(
            report_date=date(2026, 9, 18), version_id='sep', document_id='doc'),
        index=observations,
        status=product.EarningsInsightStatus(
            state='platform', freshness='fresh',
            index_release=product.EarningsInsightPartitionStatus(
                state='platform', passed=True, quality={
                    'bounded_narrative_evidence': [{
                        'theme': 'semiconductors', 'page_number': 9,
                        'char_start': 120, 'char_end': 180,
                        'text': 'Semiconductor earnings contributed to growth.',
                        'status': 'source_mention_not_inference',
                    }]})))
    monkeypatch.setattr(product, 'load_snapshot', lambda *_args, **_kwargs: snapshot)
    products = SimpleNamespace(unstructured=SimpleNamespace(document_pages=lambda _: []))
    packet = product.load_analysis_packet(products)
    assert packet.observation_count == 2
    assert {item.period for group in packet.observation_groups.values()
            for item in group} == {'2026Q2', '2026Q3'}
    assert packet.narrative_evidence[0].topic == 'semiconductors'
    assert packet.narrative_evidence[0].page_number == 9
    rendered = render_macro_analysis_packet(packet)
    assert '2026Q2' in rendered and '2026Q3' in rendered
    assert '半导体原文提及' in rendered


def test_sector_legacy_interface_does_not_call_technology_scope_full_market(monkeypatch):
    snapshot = product.EarningsInsightSnapshot(
        report=product.EarningsInsightReport(
            report_date=date(2026, 9, 18), version_id='sep'),
        status=product.EarningsInsightStatus(
            state='platform', freshness='fresh',
            sector_release=product.EarningsInsightPartitionStatus(
                state='registered_no_data', passed=False, quality={
                    'core_acceptance': 'passed', 'report_coverage': 'partial'})))
    monkeypatch.setenv('ATS_STRUCTURED_SECTOR_FACTSET_MODE', 'platform')
    monkeypatch.setattr(factset, '_platform_snapshot', lambda products=None: snapshot)
    material = factset.fetch_sector_material()
    assert material['text'] == ''
    assert '十一行业分区' in material['reason']
    assert material['version_id'] == 'sep'


def _products(tmp_path):
    repository = SQLiteStructuredRepository(
        tmp_path / 'structured.sqlite', artifact_root=tmp_path / 'artifacts')
    repository.bootstrap_catalog()
    products = DataProducts(
        structured_repository=repository,
        unstructured_repository=SimpleNamespace(documents_by_id=lambda _: {}))
    return repository, products


def _release(repository, *, version, report_date, known_at, status, passed,
             quality=None):
    return repository.save_release_manifest(
        source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight', partition='index_core',
        report_date=report_date, document_id='doc-' + version,
        version_id=version, artifact_id='artifact-' + version,
        known_at=known_at, extractor_version='fixture-' + version,
        status=status, passed=passed, quality=quality or {}, observation_ids=[])


def test_new_shadow_report_keeps_previous_release_explicitly_historical(tmp_path):
    repository, products = _products(tmp_path)
    first = datetime(2026, 9, 16, tzinfo=timezone.utc)
    _release(repository, version='prior', report_date='2026-09-15',
             known_at=first, status='platform', passed=True)
    _release(repository, version='current', report_date='2026-09-18',
             known_at=first + timedelta(days=1), status='shadow', passed=False,
             quality={'review_reasons': ['independent_index_review_required']})
    historical_asof = products.earnings_insight_snapshot(as_of=first)
    current_asof = products.earnings_insight_snapshot(
        as_of=first + timedelta(days=2))
    assert historical_asof.report.version_id == 'prior'
    assert historical_asof.status.state == 'platform'
    assert current_asof.report.version_id == 'prior'
    assert current_asof.status.state == 'stale'
    assert 'latest_unpublished_report:2026-09-18' in current_asof.status.warnings
    repository.close()


def test_shadow_only_is_pending_review_not_never_had_data(tmp_path):
    repository, products = _products(tmp_path)
    _release(repository, version='current', report_date='2026-09-18',
             known_at=NOW, status='shadow', passed=False,
             quality={'review_reasons': ['independent_index_review_required']})
    snapshot = products.earnings_insight_snapshot(as_of=NOW)
    assert not snapshot.report.version_id
    assert snapshot.status.state == 'pending_review'
    assert snapshot.status.index_release.state == 'shadow'
    repository.close()


def test_explicit_product_version_rollback_keeps_history_and_fails_closed(
        tmp_path, monkeypatch):
    repository, products = _products(tmp_path)
    first = datetime(2026, 9, 16, tzinfo=timezone.utc)
    _release(repository, version='aug', report_date='2026-08-28',
             known_at=first, status='platform', passed=True)
    _release(repository, version='sep', report_date='2026-09-18',
             known_at=first + timedelta(days=1), status='platform', passed=True)
    overlay = tmp_path / 'releases.yaml'
    monkeypatch.setenv('ATS_STRUCTURED_RELEASE_FILE', str(overlay))
    manager = ReleaseManager(repository, path=overlay)
    as_of = first + timedelta(days=2)
    assert products.earnings_insight_snapshot(as_of=as_of).report.version_id == 'sep'
    with pytest.raises(ValueError, match='not_published'):
        manager.pin_factset_product('missing')
    manager.pin_factset_product('aug', actor='acceptance')
    assert pinned_product_version('factset_earnings_insight') == 'aug'
    assert products.earnings_insight_snapshot(as_of=as_of).report.version_id == 'aug'
    assert product.load_snapshot(products, as_of=as_of, version_id='sep').report.version_id == 'sep'
    manager.clear_factset_product_pin(actor='acceptance')
    assert products.earnings_insight_snapshot(as_of=as_of).report.version_id == 'sep'
    manager.rollback(kind='source', target_id='factset_earnings_insight_doc',
                     mode='shadow', actor='acceptance')
    manager.rollback(kind='consumer', target_id='macro_factset',
                     mode='off', actor='acceptance')
    assert source_mode('factset_earnings_insight_doc') == 'shadow'
    assert read_mode('macro_factset') == 'off'
    assert len(repository.release_manifests(dataset_id='sp500_earnings_insight',
                                            partition='index_core')) == 2
    repository.close()


def test_product_pin_cli_requires_confirmation_and_writes_isolated_overlay(
        tmp_path, capsys):
    from ats.runtime.cli import main

    repository, _ = _products(tmp_path)
    _release(repository, version='old', report_date='2026-08-28',
             known_at=NOW, status='platform', passed=True)
    repository.close()
    arguments = ['data', 'factset-product-pin', 'old', '--db',
                 str(tmp_path / 'structured.sqlite'), '--release-file',
                 str(tmp_path / 'releases.yaml')]
    with pytest.raises(ValueError, match='requires version VALUE and --confirm'):
        main(arguments)
    assert not (tmp_path / 'releases.yaml').exists()
    assert main([*arguments, '--confirm']) == 0
    assert 'old' in capsys.readouterr().out
    assert pinned_product_version('factset_earnings_insight',
                                  path=tmp_path / 'releases.yaml') == 'old'


def test_empty_and_failed_refresh_are_distinct_asof(tmp_path):
    repository, products = _products(tmp_path)
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert products.earnings_insight_snapshot(as_of=before).status.state == \
        'registered_no_data'
    run_id = repository.begin_ingestion(
        source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight', query_scope={'test': 'network_failure'})
    repository.finish_ingestion(
        run_id, status='unreachable', reason_codes={'unreachable': 1})
    after = datetime.now(timezone.utc) + timedelta(seconds=1)
    failed = products.earnings_insight_snapshot(as_of=after)
    assert failed.status.state == 'unavailable'
    assert failed.status.latest_refresh_failure == 'unreachable'
    assert products.earnings_insight_snapshot(as_of=before).status.state == \
        'registered_no_data'
    repository.close()

from datetime import datetime, timezone
from datetime import timedelta
from dataclasses import replace
from hashlib import sha256
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import pytest
import yaml
from PIL import Image

from ats.data.sources.factset_earnings_insight import FactSetFetch, inspect_pdf, STABLE_URL
from ats.data.sources.factset_earnings_text import (
    bounded_narrative_evidence, extract_index_text, extract_index_text_semantic,
    merge_candidate_evidence,
    validate_index_candidates,
)
from ats.data.core.structured_models import ArtifactDescriptor
from ats.data.pipelines.factset_earnings_insight import FactSetIndexPipeline, FactSetWeeklyPipeline
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
from ats.data.sources.factset_contracts import GroupIdentity
from ats.data.sources.factset_index_charts import extract_index_chart_candidates
from ats.data.sources.factset_report_layout import locate_chart, load_layout_policy
from ats.data.stores.structured.factset_reviews import FactSetReviews
from ats.data.sources.factset_contracts import digest
from ats.data.products.earnings_insight import load_snapshot
from types import SimpleNamespace


def test_dual_report_fixture_manifest_is_local_and_content_addressed():
    fixture = Path('tests/fixtures/factset_earnings_insight/acceptance/dual-report-fixtures.yaml')
    manifest = yaml.safe_load(fixture.read_text())
    assert manifest['production_review_approval'] is False
    assert set(manifest['reports']) == {'2026-08-28', '2026-09-18'}
    for report in manifest['reports'].values():
        body = Path(report['pdf_path']).read_bytes()
        assert sha256(body).hexdigest() == report['pdf_sha256']
        assert report['index_numeric_golden_status'] == 'independent_source_review_complete'
        assert (fixture.parent / report['index_numeric_golden']).exists()


def test_independent_key_metrics_partial_golden_matches_source_and_parser():
    fixture = Path('tests/fixtures/factset_earnings_insight/acceptance/index-key-metrics-partial.yaml')
    golden = yaml.safe_load(fixture.read_text())
    locations = yaml.safe_load(Path(
        'tests/fixtures/factset_earnings_insight/acceptance/dual-report-fixtures.yaml').read_text())
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    for report_date, expected in golden['reports'].items():
        body = Path(locations['reports'][report_date]['pdf_path']).read_bytes()
        assert sha256(body).hexdigest() == expected['pdf_hash']
        document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                             'application/pdf', body, now))
        source_page = document.pages[expected['page_number'] - 1].text
        projected = extract_index_text_semantic(
            document, document_id='doc', version_id='version', known_at=now)
        for row in expected['values']:
            assert row['anchor'] in ' '.join(source_page.split())
            matching = [candidate for candidate in projected.candidates
                        if candidate.metric_id == row['metric']
                        and candidate.period.value == row['period']]
            assert matching, (report_date, row['metric'], row['period'])
            assert any(abs(float(candidate.value) - float(row['value'])) < 1e-9
                       and candidate.unit == row['unit'] for candidate in matching)


def test_index_core_matches_independent_all_period_golden():
    base = Path('tests/fixtures/factset_earnings_insight/acceptance')
    golden = yaml.safe_load((base / 'index-core-golden.yaml').read_text())
    manifest = yaml.safe_load((base / 'dual-report-fixtures.yaml').read_text())
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    for report_date, expected in golden['reports'].items():
        body = Path(manifest['reports'][report_date]['pdf_path']).read_bytes()
        assert sha256(body).hexdigest() == expected['pdf_sha256']
        source = FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                              'application/pdf', body, now)
        document = inspect_pdf(source)
        run = extract_index_text_semantic(
            document, document_id='doc', version_id='version', known_at=now)
        run.candidates = merge_candidate_evidence(
            run.candidates + extract_index_chart_candidates(document, run)).candidates
        validated = validate_index_candidates(run)
        actual = {(c.metric_id, c.period.value): c for c in validated.candidates
                  if c.status.value == 'accepted'}
        target = {(metric, row['period']): float(value)
                  for row in expected['groups']
                  for metric, value in row['values'].items()}
        assert len(target) == expected['applicability']['applicable_metric_period_cells']
        assert set(actual) == set(target), (report_date, set(target) - set(actual),
                                            set(actual) - set(target))
        assert all(abs(float(actual[key].value) - value) < 1e-9
                   for key, value in target.items())


def test_bounded_narrative_keeps_only_explicit_report_mentions():
    for name, expected_semiconductor_page in [
        ('ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin', 12),
        ('f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin', 9),
    ]:
        body = (Path('var/data_artifacts') / name).read_bytes()
        document = inspect_pdf(FactSetFetch(
            STABLE_URL, STABLE_URL, 200, '', '', 'application/pdf', body,
            datetime(2026, 9, 28, tzinfo=timezone.utc)))
        evidence = bounded_narrative_evidence(document)
        assert any(row['theme'] == 'semiconductors'
                   and row['page_number'] == expected_semiconductor_page
                   for row in evidence)
        assert not any(row['theme'] == 'consumer_electronics' for row in evidence)
        assert all(row['text'] in ' '.join(document.pages[row['page_number'] - 1].text.split())
                   for row in evidence)
        assert all(row['status'] == 'source_mention_not_inference' for row in evidence)


def test_index_review_binds_pdf_candidates_policy_scope_and_latest_decision(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    reviews = FactSetReviews(repository)
    start = datetime(2026, 9, 28, tzinfo=timezone.utc)
    cell_key = {'metric_id': 'valuation.forward_pe', 'period': '2026-09-18',
                'period_basis': 'snapshot'}
    context = {
        'pdf_hash': 'a' * 64, 'document_version': 'doc-v1',
        'policy_hash': 'b' * 64, 'extractor_version': 'semantic-v1',
        'scope_id': 'index', 'scope_version': 'focus-v1',
        'metric_group': 'index_core', 'entity_ids': ['SP500'],
        'registered_metric_ids': ['valuation.forward_pe'],
        'cells': [{**cell_key, 'value': 19.1, 'unit': 'multiple',
                   'estimate_state': 'estimated', 'raw_token': '19.1',
                   'status': 'accepted', 'evidence_hash': 'd' * 64}],
    }
    context['candidate_set_hash'] = digest(context['cells'])
    package = reviews.register_index(
        context, expected_cells=[cell_key], not_disclosed=[],
        evidence_refs=['pdf:p12:forward-pe'], at=start)
    review = reviews.decide_index(
        package, decision='approve', reviewer='independent_reviewer',
        evidence_refs=['pdf:p12:forward-pe'], note='checked source value',
        at=start + timedelta(seconds=1))
    assert reviews.require_index_approval(
        context, review, as_of=start + timedelta(seconds=2))['package_hash'] == package
    for key, value in [('pdf_hash', 'e' * 64), ('candidate_set_hash', 'f' * 64),
                       ('policy_hash', 'g' * 64), ('scope_version', 'focus-v2'),
                       ('extractor_version', 'semantic-v2')]:
        with pytest.raises(ValueError, match='context_mismatch'):
            reviews.require_index_approval(
                {**context, key: value}, review,
                as_of=start + timedelta(seconds=2))
    with pytest.raises(ValueError, match='future'):
        reviews.require_index_approval(context, review, as_of=start)
    reviews.decide_index(
        package, decision='reject', reviewer='independent_reviewer',
        evidence_refs=['pdf:p12:forward-pe'], note='source review reversed',
        at=start + timedelta(seconds=3))
    with pytest.raises(ValueError, match='stale'):
        reviews.require_index_approval(
            context, review, as_of=start + timedelta(seconds=4))
    bad = reviews.register_index(context, expected_cells=[
        {'metric_id': 'valuation.trailing_pe', 'period': '2026-09-18',
         'period_basis': 'snapshot'}], not_disclosed=[],
        evidence_refs=['pdf:p12'], at=start)
    with pytest.raises(ValueError, match='invalid'):
        reviews.decide_index(
            bad, decision='approve', reviewer='independent_reviewer',
            evidence_refs=['pdf:p12'], note='should fail', at=start + timedelta(seconds=1))
    expanded = {**context, 'registered_metric_ids': [
        'valuation.forward_pe', 'valuation.trailing_pe']}
    missing_registered = reviews.register_index(
        expanded, expected_cells=[cell_key], not_disclosed=[],
        evidence_refs=['pdf:p12'], at=start)
    with pytest.raises(ValueError, match='index_registered_metrics_not_accounted_for'):
        reviews.decide_index(
            missing_registered, decision='approve', reviewer='independent_reviewer',
            evidence_refs=['pdf:p12'], note='incomplete registry coverage',
            at=start + timedelta(seconds=1))


def test_index_manifest_crash_replay_keeps_existing_observations(tmp_path):
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    body = path.read_bytes()
    document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                        'application/pdf', body, now))
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    repository.bootstrap_catalog()
    artifact = repository.put_artifact(body, ArtifactDescriptor(
        source_id='factset_earnings_insight_metrics', dataset_id='sp500_earnings_insight',
        fetched_at=now, source_url=STABLE_URL, media_type='application/pdf',
        retention='licensed_internal_research'))
    args = dict(document_id='doc', version_id='doc@sep', artifact_id=artifact.id,
                known_at=now, extractor_version='factset-text-semantic-v2')
    with patch.object(repository, 'save_release_manifest', side_effect=RuntimeError('crash')):
        with pytest.raises(RuntimeError, match='crash'):
            FactSetIndexPipeline(repository).run(document, **args)
    with repository._lock:
        before = repository.conn.execute(
            'SELECT COUNT(*) FROM structured_observations').fetchone()[0]
    assert before > 0
    recovered = FactSetIndexPipeline(repository).run(document, **args)
    assert recovered['release_status'] == 'shadow'
    assert recovered['accepted'] == 0
    assert recovered['unchanged'] == before
    with repository._lock:
        after = repository.conn.execute(
            'SELECT COUNT(*) FROM structured_observations').fetchone()[0]
    assert after == before


def test_two_real_reports_preserve_index_values_but_use_two_states():
    paths = [
        'var/data_artifacts/ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
        'var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
    ]
    now = datetime(2026,9,28,tzinfo=timezone.utc)
    for path in paths:
        source = FactSetFetch(STABLE_URL,STABLE_URL,200,'','','application/pdf',Path(path).read_bytes(),now)
        document = inspect_pdf(source)
        args = dict(document_id='doc',version_id='version',known_at=now)
        legacy = extract_index_text(document, **args)
        current = extract_index_text_semantic(document, **args)
        assert {(c.metric_id,c.value,c.unit,c.period.value) for c in legacy.candidates} <= {
            (c.metric_id,c.value,c.unit,c.period.value) for c in current.candidates}
        assert all(c.estimate_state.value in {'actual','estimated'} for c in current.candidates)
        assert all(c.dimensions['source_estimate_wording'] == old.estimate_state.value
                   for c,old in zip(current.candidates[:len(legacy.candidates)], legacy.candidates))
        assert all(c.dimensions['extraction_policy_hash'] for c in current.candidates)
        assert all(c.extractor_version == 'factset-text-semantic-v2' for c in current.candidates)
        assert validate_index_candidates(current).accepted > 0


def test_semantic_candidate_ids_are_stable_across_refetch_time():
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                        'application/pdf', path.read_bytes(), now))
    args = dict(document_id='doc', version_id='same-content-version')
    first = extract_index_text_semantic(document, known_at=now, **args)
    replay = extract_index_text_semantic(
        document, known_at=now + timedelta(days=1), **args)
    assert {c.candidate_id for c in first.candidates} == {
        c.candidate_id for c in replay.candidates}


def test_semantic_index_does_not_require_first_pages():
    paths = [
        'var/data_artifacts/ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
        'var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
    ]
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    for path in paths:
        source = FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '', 'application/pdf',
                              Path(path).read_bytes(), now)
        document = inspect_pdf(source)
        moved = replace(document, pages=document.pages[20:] + document.pages[:20])
        args = dict(document_id='doc', version_id='version', known_at=now)
        baseline = extract_index_text_semantic(document, **args)
        shifted = extract_index_text_semantic(moved, **args)
        key = lambda run: {(c.metric_id, c.value, c.unit, c.period.value) for c in run.candidates}
        assert key(shifted) == key(baseline)


def test_semantic_index_pipeline_stages_separate_version(tmp_path):
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    body = path.read_bytes()
    document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                         'application/pdf', body, now))
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    repository.bootstrap_catalog()
    artifact = repository.put_artifact(body, ArtifactDescriptor(
        source_id='factset_earnings_insight_metrics', dataset_id='sp500_earnings_insight',
        fetched_at=now, source_url=STABLE_URL, media_type='application/pdf',
        retention='licensed_internal_research'))
    pipeline = FactSetIndexPipeline(repository)
    args = dict(document_id='doc', version_id='doc@sep', artifact_id=artifact.id,
                known_at=now, extractor_version='factset-text-semantic-v2')
    first = pipeline.run(document, **args)
    replay = pipeline.run(document, **args)
    revised = pipeline.run(document, **{**args,
        'extractor_version': 'factset-text-semantic-v2:revised-policy'})
    assert first['accepted'] == 17
    semantic = extract_index_text_semantic(
        document, document_id='doc', version_id='doc@sep', known_at=now)
    revision = next(c for c in semantic.candidates
                    if c.metric_id == 'earnings.revision.improved_sector_count')
    assert (revision.period.value, revision.value,
            revision.dimensions['comparison_date']) == ('2026Q3', 4, '2026-06-30')
    bottom_up = next(c for c in semantic.candidates
        if c.metric_id == 'earnings.bottom_up_eps')
    assert (bottom_up.period.value, bottom_up.value, bottom_up.unit) == (
        '2026Q3', 90.03, 'currency_per_share')
    assert first['release_status'] == 'shadow'
    assert replay['status'] == 'no_change'
    assert revised['release_id'] != first['release_id']
    assert {row['extractor_version'].split(':release:')[0]
            for row in repository.release_manifests(
                dataset_id='sp500_earnings_insight', partition='index_core')} == {
        'factset-text-semantic-v2', 'factset-text-semantic-v2:revised-policy'}


def test_index_aggregate_rows_from_real_chart_labels():
    cases = [
        ('ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
         23, 3, {'earnings.margin.increase_share': .59,
                 'earnings.margin.unchanged_share': .01,
                 'earnings.margin.decrease_share': .40}),
        ('f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
         13, 2, {'earnings.eps.above_estimate_share': .87,
                 'earnings.eps.inline_estimate_share': .03,
                 'earnings.eps.below_estimate_share': .10}),
        ('ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
         30, 2, {'revenue.geographic.us_share': .59,
                 'revenue.geographic.international_share': .41}),
        ('f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
         26, 2, {'revenue.geographic.us_share': .59,
                 'revenue.geographic.international_share': .41}),
        ('f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
         14, 2, {'earnings.eps.surprise_pct': .259}),
        ('f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
         14, 3, {'earnings.revenue.surprise_pct': .031}),
        ('ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
         21, 2, {'earnings.eps.yoy_growth': .52}),
        ('f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
         22, 2, {'earnings.eps.yoy_growth': .289}),
    ]
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    policy = load_layout_policy()
    for path, page, ordinal, expected in cases:
        body = (Path('var/data_artifacts') / path).read_bytes()
        document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                             'application/pdf', body, now))
        image = next(i for i in document.images if i.page_number == page
                     and i.image_number == ordinal)
        chart = locate_chart(image, document.pages[page - 1].text, policy,
                             report_date=document.report_date)
        run = extract_index_text_semantic(document, document_id='doc',
                                          version_id='version', known_at=now)
        candidates = extract_index_chart_candidates(document, run, policy=policy,
                                                     chart_inventory=(chart,))
        actual = {c.metric_id: c.value for c in candidates}
        assert set(actual) == set(expected)
        assert all(abs(actual[metric] - value) <= 1e-9 for metric, value in expected.items())
        assert all(c.evidence[0].anchor_kind == 'image_region' for c in candidates)


def test_resized_source_chart_never_silently_relabels_index_values():
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                         'application/pdf', path.read_bytes(), now))
    source = next(image for image in document.images
                  if image.page_number == 13 and image.image_number == 2)
    original = Image.open(BytesIO(source.data))
    expected = {'earnings.eps.above_estimate_share': .87,
                'earnings.eps.inline_estimate_share': .03,
                'earnings.eps.below_estimate_share': .10}
    for scale in (0.8, 1.2):
        resized = original.resize((round(original.width * scale),
                                   round(original.height * scale)))
        buffer = BytesIO()
        resized.save(buffer, format='PNG')
        image = replace(source, data=buffer.getvalue(), media_type='image/png',
                        width=resized.width, height=resized.height)
        perturbed = replace(document, images=tuple(
            image if item == source else item for item in document.images))
        chart = locate_chart(image, document.pages[12].text, load_layout_policy(),
                             report_date=document.report_date)
        run = extract_index_text_semantic(
            perturbed, document_id='doc', version_id='version', known_at=now)
        actual = extract_index_chart_candidates(
            perturbed, run, chart_inventory=(chart,))
        values = {candidate.metric_id: candidate.value for candidate in actual}
        assert all(abs(values[key] - expected[key]) < 1e-9 for key in values)


def test_index_bottom_up_eps_uses_printed_bar_labels_across_reports():
    cases = [
        ('ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
         32, {2: {'2026': 361.24, '2027': 414.75}, 3: {'2026Q3': 89.68}}),
        ('f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
         28, {2: {'2026': 362.41, '2027': 417.35}, 3: {'2026Q3': 90.03}}),
    ]
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    policy = load_layout_policy()
    for path, page, charts in cases:
        body = (Path('var/data_artifacts') / path).read_bytes()
        document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                            'application/pdf', body, now))
        run = extract_index_text_semantic(document, document_id='doc',
                                          version_id='version', known_at=now)
        for ordinal, expected in charts.items():
            source = next(i for i in document.images if i.page_number == page
                          and i.image_number == ordinal)
            chart = locate_chart(source, document.pages[page - 1].text, policy,
                                 report_date=document.report_date)
            result = extract_index_chart_candidates(document, run, policy=policy,
                                                    chart_inventory=(chart,))
            assert {c.period.value: c.value for c in result} == expected
            assert all(c.evidence[0].anchor_kind == 'image_region' for c in result)


def test_semantic_index_chart_staging_remains_shadow(tmp_path):
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    body = path.read_bytes()
    document = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
                                         'application/pdf', body, now))
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    repository.bootstrap_catalog()
    artifact = repository.put_artifact(body, ArtifactDescriptor(
        source_id='factset_earnings_insight_metrics', dataset_id='sp500_earnings_insight',
        fetched_at=now, source_url=STABLE_URL, media_type='application/pdf',
        retention='licensed_internal_research'))
    result = FactSetIndexPipeline(repository).run(
        document, document_id='doc', version_id='doc@sep', artifact_id=artifact.id,
        known_at=now, extractor_version='factset-text-semantic-v2',
        include_chart_candidates=True)
    assert result['release_status'] == 'shadow'
    assert result['accepted'] >= 35
    assert result['quality']['value_checks_passed']
    assert not result['quality']['passed']
    assert result['quality']['review_reasons'] == ['independent_index_review_required']
    assert result['quality']['groups']['geography']['admitted'] == 2
    golden = yaml.safe_load(Path(
        'tests/fixtures/factset_earnings_insight/acceptance/index-core-golden.yaml'
    ).read_text())['reports']['2026-09-18']
    expected_cells = [
        {'metric_id': metric, 'period': row['period'],
         'period_basis': row.get('basis', 'target_quarter')}
        for row in golden['groups'] for metric in row['values']
    ]
    reviews = FactSetReviews(repository)
    package_hash = reviews.register_index(
        result['review_context'], expected_cells=expected_cells,
        not_disclosed=[{'metric_id': 'earnings.reporting.coverage',
                        'period': '2026Q3', 'period_basis': 'target_quarter'}],
        evidence_refs=['local_pdf:sha256:' + document.pdf_hash], at=now)
    review_id = reviews.decide_index(
        package_hash, decision='approve', reviewer='acceptance_test_operator',
        evidence_refs=['independent_index_core_golden'],
        note='isolated approval of exact 43-cell source inventory',
        at=now + timedelta(seconds=1))
    approved = FactSetIndexPipeline(repository).run(
        document, document_id='doc', version_id='doc@sep', artifact_id=artifact.id,
        known_at=now, extractor_version='factset-text-semantic-v2',
        include_chart_candidates=True, index_review_id=review_id,
        review_as_of=now + timedelta(seconds=2))
    assert approved['quality']['passed']
    assert approved['release_status'] == 'platform'
    assert approved['release_id'] != result['release_id']
    assert approved['quality']['review_package_hash'] == package_hash
    assert repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='index_core',
        as_of=now + timedelta(milliseconds=500), passed_only=False)[0]['status'] == 'shadow'
    assert repository.latest_release(
        dataset_id='sp500_earnings_insight', partition='index_core',
        as_of=now + timedelta(seconds=2))['status'] == 'platform'
    products = SimpleNamespace(
        structured=repository,
        unstructured=SimpleNamespace(documents_by_id=lambda _: {}))
    assert load_snapshot(products,
                         as_of=now + timedelta(seconds=2),
                         version_id='doc@sep').report.version_id == 'doc@sep'
    reviews.decide_index(
        package_hash, decision='reject', reviewer='acceptance_test_operator',
        evidence_refs=['independent_index_core_golden'],
        note='isolated reversal checks old approval invalidation',
        at=now + timedelta(seconds=3))
    assert not load_snapshot(products,
                             as_of=now + timedelta(seconds=4),
                             version_id='doc@sep').report.version_id
    rejected = FactSetIndexPipeline(repository).run(
        document, document_id='doc', version_id='doc@sep', artifact_id=artifact.id,
        known_at=now, extractor_version='factset-text-semantic-v2',
        include_chart_candidates=True, index_review_id=review_id,
        review_as_of=now + timedelta(seconds=4))
    assert rejected['release_status'] == 'shadow'
    assert rejected['release_id'] not in {result['release_id'], approved['release_id']}
    assert repository.latest_release(
        dataset_id='sp500_earnings_insight', partition='index_core',
        as_of=now + timedelta(seconds=2))['release_id'] == approved['release_id']


def test_semantic_weekly_entry_stages_without_boolean_approval(tmp_path):
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    documents = PlatformUnstructuredRepository(tmp_path / 'documents.sqlite', writable=True)
    policy = load_layout_policy()
    group = GroupIdentity(
        chart_id='earnings_revenue_scorecard', period='2026Q2',
        period_basis='target_quarter', scope_id='technology',
        scope_version=policy['scope_policy']['version'], entity_ids=('GICS_45',))
    missing = GroupIdentity(
        chart_id='eps_guidance', period='2026Q4', period_basis='target_quarter',
        scope_id='technology', scope_version=policy['scope_policy']['version'],
        entity_ids=('GICS_45',))
    all_sectors = GroupIdentity(
        chart_id='earnings_revenue_scorecard', period='2026Q2',
        period_basis='target_quarter', scope_id='all_sectors',
        scope_version=policy['scope_policy']['version'],
        entity_ids=tuple(policy['scope_policy']['scopes']['all_sectors']['entities']))
    result = FactSetWeeklyPipeline(repository, documents, clock=lambda: now).run_semantic(
        local_pdf=path, expected_groups=[group, missing, all_sectors])
    assert result['status'] == 'partial'
    assert result['core_acceptance'] == 'blocked'
    assert result['report_coverage'] == 'partial'
    assert result['sector_core']['quality']['state'] == 'partial'
    assert result['sector_core']['quality']['missing_technology_chart_ids']
    assert repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='sector_core')[0]['status'] == 'shadow'
    assert result['index_core']['release_status'] == 'shadow'
    assert len(result['sector_groups']) == 3
    assert result['sector_groups'][group.key]['quality']['observed_cells'] == 6
    assert result['sector_groups'][missing.key]['quality']['observed_cells'] == 0
    assert result['sector_groups'][all_sectors.key]['quality']['observed_cells'] > 6
    assert 'sector_group_not_located' in result['sector_groups'][missing.key]['quality']['reason_codes']
    assert result['provenance']['pdf_sha256'] == path.stem
    discovery = repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='semantic_discovery')
    assert len(discovery) == 1
    assert discovery[0]['release_id'] == result['chart_discovery_release_id']
    assert discovery[0]['status'] == 'shadow'
    assert discovery[0]['quality']['charts']
    for group_key in result['sector_groups']:
        assert repository.release_manifests(
            dataset_id='sp500_earnings_insight',
            partition='sector_group:' + group_key)


def _technology_source_golden(base, report_date):
    expected = {}
    for name in ('scorecard-visual.yaml', 'surprise-visual.yaml',
                 'margin-visual.yaml', 'guidance-visual.yaml',
                 'valuation-ratings-visual.yaml'):
        for chart in yaml.safe_load((base / name).read_text())['charts']:
            if chart['report_date'] != report_date:
                continue
            rows = [values for label, values in chart['rows'].items() if 'Tech' in label]
            assert len(rows) == 1
            for column, value in zip(chart['columns'], rows[0]):
                amount = Decimal(str(value))
                if column.endswith('_share') or column in {
                    'eps_above', 'eps_inline', 'eps_below',
                    'revenue_above', 'revenue_inline', 'revenue_below',
                }:
                    amount /= 100
                expected[(chart['page'], chart['image'], column)] = amount
    for chart in yaml.safe_load((base / 'cross-report-visual-partial.yaml').read_text())['tables']:
        if chart['report_date'] != report_date or chart.get('source_row') not in (None, 'Today'):
            continue
        rows = [value for label, value in chart['values'].items() if 'Tech' in label]
        assert len(rows) == 1
        expected[(chart['page'], chart['image'], chart['column'])] = Decimal(str(rows[0]))
    return expected


@pytest.mark.parametrize('report_date', ['2026-08-28', '2026-09-18'])
def test_semantic_core_passes_with_exact_index_and_technology_reviews(tmp_path, report_date):
    base = Path('tests/fixtures/factset_earnings_insight/acceptance')
    manifest = yaml.safe_load((base / 'dual-report-fixtures.yaml').read_text())
    inventory = yaml.safe_load((base / 'core-inventory.yaml').read_text())
    golden = yaml.safe_load((base / 'index-core-golden.yaml').read_text())
    path = Path(manifest['reports'][report_date]['pdf_path'])
    policy = load_layout_policy()
    expected_groups = [GroupIdentity(
        chart_id=chart_id, period=item['period'], period_basis=item['basis'],
        scope_id='technology', scope_version=policy['scope_policy']['version'],
        entity_ids=('GICS_45',))
        for chart_id, periods in inventory['reports'][report_date]['groups'].items()
        for item in periods]
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    current = [now]
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    documents = PlatformUnstructuredRepository(tmp_path / 'documents.sqlite', writable=True)
    pipeline = FactSetWeeklyPipeline(repository, documents, clock=lambda: current[0])
    staged = pipeline.run_semantic(local_pdf=path, expected_groups=expected_groups)
    assert staged['core_acceptance'] == 'blocked'
    assert len(staged['sector_groups']) == 12
    reviews = FactSetReviews(repository)
    cells = [{'metric_id': metric, 'period': row['period'],
              'period_basis': row.get('basis', 'target_quarter')}
             for row in golden['reports'][report_date]['groups']
             for metric in row['values']]
    index_package = reviews.register_index(
        staged['index_core']['review_context'], expected_cells=cells,
        not_disclosed=([{'metric_id': 'earnings.reporting.coverage',
                         'period': '2026Q3', 'period_basis': 'target_quarter'}]
                       if report_date == '2026-09-18' else []),
        evidence_refs=['source_pdf:' + path.stem], at=now)
    index_review = reviews.decide_index(
        index_package, decision='approve', reviewer='isolated_test',
        evidence_refs=['independent_index_core_golden'], note='isolated source review',
        at=now + timedelta(seconds=1))
    sector_reviews = {}
    for group in expected_groups:
        package_hash = staged['sector_groups'][group.key]['package_hash']
        sector_reviews[group.key] = reviews.decide(
            package_hash, decision='approve', reviewer='isolated_test',
            evidence_refs=['independent_sector_visual_golden'],
            note='isolated source review', policy=policy,
            at=now + timedelta(seconds=1))
    current[0] = now + timedelta(seconds=2)
    released = pipeline.run_semantic(
        local_pdf=path, expected_groups=expected_groups,
        review_ids=sector_reviews, index_review_id=index_review)
    assert released['core_acceptance'] == 'passed'
    assert released['report_coverage'] == 'partial'
    assert released['index_core']['release_status'] == 'platform'
    assert all(row['release_status'] == 'platform'
               for row in released['sector_groups'].values())
    assert released['sector_core']['quality']['core_acceptance'] == 'passed'
    assert released['sector_core']['quality']['state'] == 'partial'
    assert released['sector_core']['status'] == 'shadow'
    assert released['index_core']['accepted'] == 0
    assert released['index_core']['unchanged'] == len(
        staged['index_core']['review_context']['cells'])
    expected_index = {(metric, row['period']): Decimal(str(value))
                      for row in golden['reports'][report_date]['groups']
                      for metric, value in row['values'].items()}
    released_index = {
        (item['metric_id'], item['period']): Decimal(str(item['value']))
        for oid in released['index_core']['observation_ids']
        if (item := repository.observation(oid)) is not None
    }
    assert set(released_index) == set(expected_index)
    assert all(abs(released_index[key] - expected_index[key]) < Decimal('0.000000001')
               for key in expected_index)
    expected_sector = _technology_source_golden(base, report_date)
    published_sector = {}
    for group in expected_groups:
        release = released['sector_groups'][group.key]
        package = reviews.package(release['package_hash'])
        assert len(release['observation_ids']) == len(package.candidates)
        for cell in package.candidates:
            anchor = cell.value_evidence[0]
            key = (anchor.page_number, anchor.image_number, cell.column)
            assert key not in published_sector
            published_sector[key] = cell.value
    assert published_sector == expected_sector


def test_semantic_discovery_exception_keeps_document_and_failure(tmp_path):
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    documents = PlatformUnstructuredRepository(tmp_path / 'documents.sqlite', writable=True)
    policy = load_layout_policy()
    group = GroupIdentity(
        chart_id='earnings_revenue_scorecard', period='2026Q2',
        period_basis='target_quarter', scope_id='technology',
        scope_version=policy['scope_policy']['version'], entity_ids=('GICS_45',))
    with patch('ats.data.sources.factset_report_layout.discover_charts',
               side_effect=RuntimeError('simulated OCR failure')):
        result = FactSetWeeklyPipeline(repository, documents, clock=lambda: now).run_semantic(
            local_pdf=path, expected_groups=[group])
    assert result['reason_codes'] == ['chart_discovery_exception:RuntimeError']
    assert result['document']['document_version_id']
    assert result['core_acceptance'] == 'blocked'
    failure = repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='semantic_discovery')
    assert failure[0]['quality']['reason_codes'] == result['reason_codes']
    assert not repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='index_core')
    recovered = FactSetWeeklyPipeline(repository, documents, clock=lambda: now).run_semantic(
        local_pdf=path, expected_groups=[group])
    assert recovered['chart_discovery_release_id'] != result['chart_discovery_release_id']
    retained = repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='semantic_discovery')
    assert len(retained) == 2
    assert any(row['quality'].get('reason_codes') == result['reason_codes'] for row in retained)
    assert recovered['sector_groups'][group.key]['quality']['observed_cells'] == 6
    replay = FactSetWeeklyPipeline(repository, documents, clock=lambda: now).run_semantic(
        local_pdf=path, expected_groups=[group])
    assert replay['document']['document_version_id'] == recovered['document']['document_version_id']
    assert replay['chart_discovery_release_id'] == recovered['chart_discovery_release_id']
    assert replay['index_core']['release_id'] == recovered['index_core']['release_id']
    assert replay['sector_groups'][group.key]['release_id'] == recovered['sector_groups'][group.key]['release_id']


def test_semantic_index_and_sector_exceptions_remain_independent(tmp_path):
    path = Path('var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin')
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    repository = SQLiteStructuredRepository(tmp_path / 'structured.sqlite',
                                            artifact_root=tmp_path / 'artifacts')
    documents = PlatformUnstructuredRepository(tmp_path / 'documents.sqlite', writable=True)
    policy = load_layout_policy()
    group = GroupIdentity(
        chart_id='earnings_revenue_scorecard', period='2026Q2',
        period_basis='target_quarter', scope_id='technology',
        scope_version=policy['scope_policy']['version'], entity_ids=('GICS_45',))
    with patch.object(FactSetIndexPipeline, 'run', side_effect=RuntimeError('index fault')):
        with patch('ats.data.sources.factset_semantic.extract_sector_packages',
                   side_effect=ValueError('sector fault')):
            result = FactSetWeeklyPipeline(repository, documents, clock=lambda: now).run_semantic(
                local_pdf=path, expected_groups=[group])
    assert result['core_acceptance'] == 'blocked'
    assert result['index_core']['reason_codes'] == ['index_extraction_exception:RuntimeError']
    assert set(result['sector_groups'][group.key]['quality']['reason_codes']) == {
        'group_coverage_mismatch', 'independent_review_required',
        'sector_extraction_exception:ValueError'}
    assert repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='semantic_index_stage_error')
    assert repository.release_manifests(
        dataset_id='sp500_earnings_insight', partition='sector_group:' + group.key)

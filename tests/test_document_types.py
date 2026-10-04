import pytest

from ats.data.document_types import (
    CarrierFormat,
    DocumentSemantic,
    compatible_type_values,
    infer_carrier_format,
    legacy_type,
    semantic_type,
)


@pytest.mark.parametrize(
    ("legacy", "semantic"),
    [
        ("article", DocumentSemantic.RESEARCH_ARTICLE),
        ("news", DocumentSemantic.NEWS_ITEM),
        ("release", DocumentSemantic.COMPANY_RELEASE),
        ("deck", DocumentSemantic.INVESTOR_PRESENTATION),
        ("transcript", DocumentSemantic.EARNINGS_TRANSCRIPT),
        ("filing", DocumentSemantic.REGULATORY_FILING),
    ],
)
def test_legacy_document_types_have_deterministic_two_way_mapping(legacy, semantic):
    assert semantic_type(legacy) is semantic
    assert semantic_type(semantic.value) is semantic
    assert legacy_type(semantic) == legacy
    assert compatible_type_values(legacy) == (semantic.value, legacy)


def test_business_semantics_are_independent_of_pdf_carrier():
    assert infer_carrier_format("earnings-release.pdf") is CarrierFormat.PDF
    assert infer_carrier_format("investor-presentation.pdf") is CarrierFormat.PDF
    assert semantic_type("release") is DocumentSemantic.COMPANY_RELEASE
    assert semantic_type("deck") is DocumentSemantic.INVESTOR_PRESENTATION


def test_carrier_format_handles_email_html_and_structured_text():
    assert infer_carrier_format("newsletter.eml") is CarrierFormat.EMAIL
    assert infer_carrier_format("article.html") is CarrierFormat.HTML
    assert infer_carrier_format("transcript.parquet") is CarrierFormat.STRUCTURED_TEXT
    assert infer_carrier_format("manual.md") is CarrierFormat.PLAIN_TEXT

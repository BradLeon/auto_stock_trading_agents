import pytest
from ats.data.sources.factset_earnings_insight import report_refresh_url, STABLE_URL

URL = 'https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_091826.pdf'

def test_default_and_official_historical_report():
    assert report_refresh_url({}) == STABLE_URL
    assert report_refresh_url({'url': URL}) == URL

@pytest.mark.parametrize('url', [
    URL.replace('https:', 'http:'),
    URL.replace('advantage.factset.com', 'example.com'),
    URL.replace('advantage.factset.com', 'user@advantage.factset.com'),
    URL.replace('advantage.factset.com', 'advantage.factset.com:443'),
    URL + '?redirect=https://example.com', URL + '#x',
    URL.replace('091826', '023126'), URL.replace('EarningsInsight_', '../EarningsInsight_'),
    URL.replace('.pdf', '.html'), 123,
])
def test_unapproved_scopes_rejected(url):
    with pytest.raises(ValueError):
        report_refresh_url({'url': url})

@pytest.mark.parametrize('scope', [[], {'override': True}, {'url': URL, 'mode': 'force'}])
def test_unknown_options_rejected(scope):
    with pytest.raises(ValueError):
        report_refresh_url(scope)

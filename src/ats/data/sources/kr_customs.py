"""Korea Customs Service — monthly semiconductor exports. NOT YET IMPLEMENTED.

Declared because it is the single most on-mechanism third-party series for this theme:
it measures SK hynix's and Samsung's ACTUAL shipments, monthly (with 1-10 and 1-20 day
interim prints), from a body with no position in the trade.

Blocked on credentials, not on code. KOSIS answers an unauthenticated request with
`{err:"10", errMsg:"인증KEY값이 누락되었습니다"}` — the key is missing. Register at
https://kosis.kr/openapi/ and put the key in `.env` as `KR_CUSTOMS_API_KEY`; this
module then needs its query filled in against HS 8542.

Until then it returns [] and `chain.sources.collect` records a gap. That is the point:
a declared source we cannot reach must read as "we have no Korean data", never as
"Korean exports say nothing".
"""

from __future__ import annotations

import logging

from ...schemas.chain import SeriesPoint

log = logging.getLogger("ats.data.sources.kr_customs")


def fetch(*, lookback_months: int = 6, **_) -> list[SeriesPoint]:
    from ...config import get_config

    if not getattr(get_config().secrets, "kr_customs_api_key", ""):
        log.info("kr_customs: KR_CUSTOMS_API_KEY 未配置 —— 记为缺口，见模块 docstring")
        return []
    log.warning("kr_customs: key 已配置但查询尚未实现 —— 记为缺口")
    return []

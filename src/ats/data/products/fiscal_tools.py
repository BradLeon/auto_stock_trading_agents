"""财年标签纯函数工具（Phase D 8.4：归入数据产品工具层）。

`ats.data.fiscal` 只做标签字符串解析与财报期校验，不访问任何 Provider；
agents 一律从这里导入，采集层（data.collection）内部仍直接用底层实现。
"""

from __future__ import annotations

from ..fiscal import canonical_tag, parse_label, verify_transcript

__all__ = ["canonical_tag", "parse_label", "verify_transcript"]

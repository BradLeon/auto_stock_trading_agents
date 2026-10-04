"""层键的更名与拆分：legacy_keys 解析、限额一致性、layer_groups 读取。

拆层的失效模式是**静默**的：一条历史行的层键指向一个已经不存在的层，读取时被悄悄丢掉，
于是拆分前的那段历史看起来"从来没有过"。这里锁住的就是那条路径。
"""

import logging

import pytest

from ats.config import load_sector_config, reset_config_cache
from ats.schemas.sector import SectorConfig, SectorLayer


def _cfg() -> SectorConfig:
    return SectorConfig(name="demo", layers=[
        SectorLayer(key="L6_memory", label="存储", legacy_keys=["L5_fab"]),
        SectorLayer(key="L7_foundry_pkg", label="代工与先进封装", legacy_keys=["L5_fab"]),
        SectorLayer(key="L8_equipment", label="设备", legacy_keys=["L6_equipment"]),
    ])


def test_split_layer_key_resolves_to_both_halves():
    # L5_fab 拆成存储与代工：两层查历史都必须看得见那段合并口径的记录。
    keys = [ly.key for ly in _cfg().layers_by_key("L5_fab")]
    assert keys == ["L6_memory", "L7_foundry_pkg"]


def test_current_key_wins_over_a_legacy_namesake():
    # L6_equipment 是 L8 的旧键，而 L6_memory 是当前键。问 L6_memory 必须拿到它自己，
    # 绝不能因为字符串相近落到继承旧键的那一层。
    cfg = _cfg()
    assert cfg.layer_by_key("L6_memory").key == "L6_memory"
    assert [ly.key for ly in cfg.layers_by_key("L6_equipment")] == ["L8_equipment"]


def test_unknown_key_resolves_to_nothing_rather_than_raising():
    cfg = _cfg()
    assert cfg.layers_by_key("L9_nope") == []
    assert cfg.layer_by_key("L9_nope") is None


def test_is_legacy_key_marks_pre_split_rows():
    cfg = _cfg()
    assert cfg.is_legacy_key("L5_fab") is True        # 只能通过 legacy_keys 到达
    assert cfg.is_legacy_key("L6_memory") is False    # 当前键


def _write(tmp_path, risk_yaml: str, sector_yaml: str):
    (tmp_path / "sectors").mkdir(exist_ok=True)
    (tmp_path / "settings.yaml").write_text("environment: paper\n", encoding="utf-8")
    (tmp_path / "risk.yaml").write_text(risk_yaml, encoding="utf-8")
    (tmp_path / "sectors" / "demo.yaml").write_text(sector_yaml, encoding="utf-8")


SECTOR_TWO_LAYERS = """name: demo
layers:
  - key: L6_memory
    label: 存储
    legacy_keys: [L5_fab]
    tickers: [{symbol: MU}]
  - key: L7_foundry_pkg
    label: 代工
    legacy_keys: [L5_fab]
    tickers: [{symbol: TSM}]
"""


def test_layer_without_a_cap_warns_and_keeps_no_ceiling_of_its_own(tmp_path, monkeypatch, caplog):
    _write(tmp_path, """sector_layer_caps:
  demo:
    L6_memory: {weight_cap: 0.25}
""", SECTOR_TWO_LAYERS)
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    reset_config_cache()
    try:
        with caplog.at_level(logging.WARNING, logger="ats.config"):
            cfg = load_sector_config("demo")
        assert "L7_foundry_pkg" in caplog.text
        # 无限额的层不是"无上限"——weight_cap 留空，由 cross_section 落到保守默认值。
        assert cfg.layer_by_key("L7_foundry_pkg").weight_cap is None
    finally:
        reset_config_cache()


def test_stale_cap_for_a_renamed_layer_warns(tmp_path, monkeypatch, caplog):
    _write(tmp_path, """sector_layer_caps:
  demo:
    L6_memory: {weight_cap: 0.25}
    L7_foundry_pkg: {weight_cap: 0.20}
    L5_fab: {weight_cap: 0.30}
""", SECTOR_TWO_LAYERS)
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    reset_config_cache()
    try:
        with caplog.at_level(logging.WARNING, logger="ats.config"):
            load_sector_config("demo")
        # 拆分后遗留的 L5_fab 限额已经不管着任何层了 —— 它必须被报出来。
        assert "L5_fab" in caplog.text
    finally:
        reset_config_cache()


def test_layer_groups_load_and_drop_unknown_members(tmp_path, monkeypatch, caplog):
    _write(tmp_path, """sector_layer_caps:
  demo:
    L6_memory: {weight_cap: 0.25}
    L7_foundry_pkg: {weight_cap: 0.20}
layer_groups:
  demo:
    fab_memory:
      label: 制造与存储
      layers: [L6_memory, L7_foundry_pkg, L9_ghost]
      weight_cap: 0.30
""", SECTOR_TWO_LAYERS)
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    reset_config_cache()
    try:
        with caplog.at_level(logging.WARNING, logger="ats.config"):
            cfg = load_sector_config("demo")
        group = cfg.layer_groups[0]
        assert group.key == "fab_memory" and group.weight_cap == 0.30
        assert group.layers == ["L6_memory", "L7_foundry_pkg"]   # 幽灵成员被剔除
        assert "L9_ghost" in caplog.text
        # 子层之和 (0.45) > group cap (0.30) 是**有意的**：允许在两层间倾斜，合计仍被卡住。
        assert sum(ly.weight_cap for ly in cfg.layers) > group.weight_cap
    finally:
        reset_config_cache()

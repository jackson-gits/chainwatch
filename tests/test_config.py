"""Spec 17.2 (test_config): config load/overrides/env/hash/validate error cases in chainwatch/config.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from chainwatch import config
from chainwatch.config import ConfigError, canonical_json, config_hash, load, resolve_path, sha256_file


def test_to_dict_round_trip() -> None:
    cfg = load()
    d = cfg.to_dict()
    assert isinstance(d, dict)
    assert d["project"]["name"] == "chainwatch"


def test_get_path_and_attrs() -> None:
    cfg = load()
    assert cfg.get_path("project.seed") == cfg.project.seed
    assert cfg.get_path("nonexistent.key", "x") == "x"
    assert cfg.world.n_campaigns == int(cfg.get_path("world.n_campaigns"))


def test_with_overrides_returns_new_config() -> None:
    base = load()
    new = base.with_overrides(["world.n_campaigns=7", "features.priority=P0"])
    assert new.world.n_campaigns == 7
    assert new.features.priority == "P0"
    assert base.world.n_campaigns != 7


def test_with_overrides_empty() -> None:
    base = load()
    new = base.with_overrides(None)
    assert new.config_hash == base.config_hash


def test_dict_overrides() -> None:
    base = load()
    new = base.with_overrides({"world.n_campaigns": 5})
    assert new.world.n_campaigns == 5


def test_env_overrides() -> None:
    env = {"CHAINWATCH__WORLD__N_CAMPAIGNS": "9", "CHAINWATCH__SERVING__HOST": "127.0.0.1"}
    new = load(overrides=None, environ=env)
    assert new.world.n_campaigns == 9
    assert new.serving.host == "127.0.0.1"


def test_parse_value() -> None:
    assert config.parse_value("true") is True
    assert config.parse_value("false") is False
    assert config.parse_value("42") == 42
    assert config.parse_value("3.14") == 3.14
    assert config.parse_value("[1, 2, 3]") == [1, 2, 3]
    assert config.parse_value("hello world") == "hello world"
    assert config.parse_value('"quoted"') == "quoted"


def test_deep_merge() -> None:
    base = {"a": {"b": 1}, "c": 2}
    override = {"a": {"d": 3}, "c": 4}
    res = config.deep_merge(base, override)
    assert res["a"] == {"b": 1, "d": 3}
    assert res["c"] == 4


def test_resolve_path() -> None:
    root = resolve_path(".")
    assert root.is_absolute()
    assert resolve_path("data/raw") == root / "data" / "raw"
    ab = root / "configs" / "default.toml"
    assert resolve_path(ab) == ab


def test_canonical_json_stable_order() -> None:
    a = canonical_json({"b": 1, "a": 2})
    assert a == '{"a":2,"b":1}'


def test_config_hash_changes_on_change() -> None:
    base = load()
    h0 = base.config_hash
    h1 = load(overrides=["world.n_campaigns=5"]).config_hash
    assert h0 != h1


def test_sha256_file(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hi", encoding="utf-8")
    h = sha256_file(p)
    assert h.startswith("sha256:")
    assert len(h) == 71


def test_validate_mix_sum_error() -> None:
    with pytest.raises(ConfigError, match="must sum to 1"):
        load(overrides=["world.mix.funnel=0.9", "world.mix.layered_chain=0.01"])


def test_validate_mix_unknown_typology() -> None:
    with pytest.raises(ConfigError, match="unknown typologies"):
        load(overrides=["world.mix.phishing=0.1"])


def test_validate_mule_mix_sum() -> None:
    with pytest.raises(ConfigError, match="mule_mix must sum to 1"):
        load(overrides=["world.mule_mix.new=0.5"])


def test_validate_noise_level() -> None:
    with pytest.raises(ConfigError, match="noise_level"):
        load(overrides=["world.noise_level=3.0"])
    with pytest.raises(ConfigError, match="noise_level"):
        load(overrides=["world.noise_level=-1.0"])


def test_validate_windows_s() -> None:
    with pytest.raises(ConfigError, match="windows_s must be"):
        load(overrides=["features.windows_s=[10, 20]"])


def test_validate_priority() -> None:
    with pytest.raises(ConfigError, match="priority must be"):
        load(overrides=["features.priority=P2"])


def test_validate_state_ttl_too_small() -> None:
    with pytest.raises(ConfigError, match="state_ttl_s"):
        load(overrides=["features.state_ttl_s=100"])


def test_validate_fpr_bounds() -> None:
    for k in ["thresholds.fpr_verify", "thresholds.fpr_hold", "thresholds.fpr_edge"]:
        with pytest.raises(ConfigError, match="must be in"):
            load(overrides=[f"{k}=0"])
        with pytest.raises(ConfigError, match="must be in"):
            load(overrides=[f"{k}=2"])


def test_validate_hold_not_exceeds_verify() -> None:
    with pytest.raises(ConfigError, match="fpr_hold must not exceed"):
        load(overrides=["thresholds.fpr_verify=0.001", "thresholds.fpr_hold=0.01"])


def test_validate_workers_must_be_one() -> None:
    with pytest.raises(ConfigError, match="workers must be 1"):
        load(overrides=["serving.workers=2"])


def test_validate_rings_escalation() -> None:
    with pytest.raises(ConfigError, match="must be verify or hold"):
        load(overrides=["rings.escalate_out_to=alert"])


def test_validate_split_days() -> None:
    with pytest.raises(ConfigError, match="split_days must be three"):
        load(overrides=["eval.split_days=[1, 2]"])
    with pytest.raises(ConfigError, match="split_days must be three"):
        load(overrides=["eval.split_days=[3, 2, 4]"])


def test_validate_burn_in_before_train() -> None:
    with pytest.raises(ConfigError, match="burn_in_days must be before"):
        load(overrides=["eval.split_days=[3, 6, 10]", "eval.burn_in_days=5"])


def test_validate_preset_and_catch_min_action() -> None:
    with pytest.raises(ConfigError, match="unknown world.preset"):
        load(overrides=["world.preset=bogus"])
    with pytest.raises(ConfigError, match="catch_min_action must be"):
        load(overrides=["eval.catch_min_action=allow"])


def test_preset_override() -> None:
    cfg = load(preset="small")
    assert cfg.world.preset == "small"
    assert cfg.world.n_campaigns == 40


def test_config_hash_matches_canonical_json() -> None:
    cfg = load()
    assert config_hash(cfg.to_dict()) == cfg.config_hash


def test_invalid_override_format_raises() -> None:
    with pytest.raises(ConfigError, match="must look like section.key"):
        load(overrides=["not_a_dot_eq_value"])


def test_empty_override_key_raises() -> None:
    with pytest.raises(ConfigError, match="empty override key"):
        load(overrides=["=5"])


def test_nonexistent_preset_raises() -> None:
    with pytest.raises(ConfigError, match="unknown preset"):
        load(preset="nonexist")


def test_section_copy_deepcopy() -> None:
    from copy import deepcopy

    cfg = load(overrides=["world.n_campaigns=3"])
    c = deepcopy(cfg)
    c.world.n_campaigns = 99
    assert cfg.world.n_campaigns == 3
    assert c.world.n_campaigns == 99

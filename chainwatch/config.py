"""Configuration loading: TOML files, preset overrides, env overrides, validation and config hash.

Serve-env safe (stdlib only; ``tomli`` on Python 3.10).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as _toml  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "configs"
DEFAULT_CONFIG = CONFIG_DIR / "default.toml"
ENV_PREFIX = "CHAINWATCH__"


class ConfigError(ValueError):
    """Raised when a configuration is invalid."""


class Section(dict):
    """A dict with attribute access (``cfg.features.windows_s``)."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    def __deepcopy__(self, memo: dict) -> Section:
        return _to_section(copy.deepcopy(_to_plain(self), memo))


def _to_section(obj: Any) -> Any:
    if isinstance(obj, dict) and not isinstance(obj, Section):
        return Section({k: _to_section(v) for k, v in obj.items()})
    if isinstance(obj, Section):
        return Section({k: _to_section(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_section(v) for v in obj]
    return obj


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


class Config(Section):
    """Merged configuration. Sections are reachable as attributes."""

    @property
    def config_hash(self) -> str:
        return config_hash(self)

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def with_overrides(self, overrides: dict[str, Any] | list[str] | None) -> Config:
        """Return a validated copy with ``section.key=value`` overrides applied."""
        data = _to_plain(self)
        _apply_overrides(data, overrides)
        _derive(data)
        validate(data)
        return Config(_to_section(data))

    def to_dict(self) -> dict:
        return _to_plain(self)


def read_toml(path: str | Path) -> dict:
    with open(path, "rb") as fh:
        return _toml.load(fh)


def deep_merge(base: dict, override: dict) -> dict:
    """Merge ``override`` into ``base`` in place (dicts recurse, everything else replaces)."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def parse_value(text: str) -> Any:
    """Parse a TOML literal (number, bool, list, quoted string); fall back to a bare string."""
    try:
        return _toml.loads(f"v = {text}")["v"]
    except Exception:
        return text


def _set_dotted(data: dict, dotted: str, value: Any) -> None:
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        raise ConfigError(f"empty override key: {dotted!r}")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _apply_overrides(data: dict, overrides: dict[str, Any] | list[str] | None) -> None:
    if not overrides:
        return
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            _set_dotted(data, key, value)
        return
    for item in overrides:
        if "=" not in item:
            raise ConfigError(f"override must look like section.key=value, got {item!r}")
        key, text = item.split("=", 1)
        _set_dotted(data, key.strip(), parse_value(text.strip()))


def _env_overrides(environ: dict[str, str]) -> dict[str, Any]:
    out = {}
    for name in sorted(environ):
        if name.startswith(ENV_PREFIX):
            dotted = ".".join(p.lower() for p in name[len(ENV_PREFIX) :].split("__"))
            out[dotted] = parse_value(environ[name])
    return out


def _derive(data: dict) -> None:
    """Fill derived values that must stay consistent across sections."""
    world = data.setdefault("world", {})
    if "campaign_start_min_day" not in world:
        world["campaign_start_min_day"] = float(data.get("eval", {}).get("burn_in_days", 3))


def validate(data: dict) -> None:
    """Raise ConfigError listing every problem found."""
    problems: list[str] = []
    world = data.get("world", {})
    mix = world.get("mix", {})
    if mix and abs(sum(float(v) for v in mix.values()) - 1.0) > 1e-6:
        problems.append(f"world.mix must sum to 1 (got {sum(mix.values()):.6f})")
    known = {"funnel", "layered_chain", "split_merge", "round_trip", "structuring", "slow_roll"}
    if set(mix) - known:
        problems.append(f"unknown typologies in world.mix: {sorted(set(mix) - known)}")
    mm = world.get("mule_mix", {})
    if mm and abs(sum(float(v) for v in mm.values()) - 1.0) > 1e-6:
        problems.append("world.mule_mix must sum to 1")
    nl = float(world.get("noise_level", 1.0))
    if not 0.0 <= nl <= 2.0:
        problems.append("world.noise_level must be in [0, 2]")
    if world.get("preset") not in (None, "small", "medium", "large", "tiny", "demo", "custom"):
        problems.append(f"unknown world.preset {world.get('preset')!r}")
    feats = data.get("features", {})
    if feats and list(feats.get("windows_s", [])) != [600, 3600, 86400]:
        problems.append("features.windows_s must be [600, 3600, 86400] (feature names are fixed)")
    if feats and feats.get("priority") not in ("P0", "P1"):
        problems.append("features.priority must be P0 or P1")
    if feats and feats.get("state_ttl_s", 0) < max(
        feats.get("windows_s", [0]) + [feats.get("fifo_lookback_s", 0)]
    ):
        problems.append("features.state_ttl_s must be at least the longest window and the FIFO lookback")
    thr = data.get("thresholds", {})
    for key in ("fpr_verify", "fpr_hold", "fpr_edge"):
        if key in thr and not 0.0 < float(thr[key]) < 1.0:
            problems.append(f"thresholds.{key} must be in (0, 1)")
    if thr and float(thr.get("fpr_hold", 0)) > float(thr.get("fpr_verify", 1)):
        problems.append("thresholds.fpr_hold must not exceed thresholds.fpr_verify")
    serving = data.get("serving", {})
    if serving and int(serving.get("workers", 1)) != 1:
        problems.append("serving.workers must be 1 (stateful engine, rule E8)")
    rings = data.get("rings", {})
    for key in ("escalate_out_to", "escalate_in_to"):
        if key in rings and rings[key] not in ("verify", "hold"):
            problems.append(f"rings.{key} must be verify or hold")
    ev = data.get("eval", {})
    sd = ev.get("split_days", [15, 20, 30])
    if not (len(sd) == 3 and sd[0] < sd[1] < sd[2]):
        problems.append("eval.split_days must be three increasing day numbers")
    if ev and float(ev.get("burn_in_days", 0)) >= float(sd[0]):
        problems.append("eval.burn_in_days must be before the end of the training split")
    if ev and ev.get("catch_min_action") not in (None, "verify", "hold"):
        problems.append("eval.catch_min_action must be verify or hold")
    if problems:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(problems))


def load(
    path: str | Path | None = None,
    preset: str | None = None,
    overrides: dict[str, Any] | list[str] | None = None,
    env: bool = True,
    environ: dict[str, str] | None = None,
) -> Config:
    """Load ``default.toml`` (or ``path``), a world preset, feature flags, env and explicit overrides."""
    path = Path(path) if path else DEFAULT_CONFIG
    data = read_toml(path)
    preset_name = preset
    if preset_name:
        preset_file = CONFIG_DIR / f"world_{preset_name}.toml"
        if not preset_file.exists():
            raise ConfigError(f"unknown preset {preset_name!r} (no {preset_file.name})")
        deep_merge(data, read_toml(preset_file))
    flags_file = CONFIG_DIR / "features.toml"
    if "feature_flags" not in data:
        data["feature_flags"] = read_toml(flags_file) if flags_file.exists() else {}
    data["feature_flags"].setdefault("disabled", [])
    data["feature_flags"].setdefault("lower_to_p1", [])
    if env:
        _apply_overrides(data, _env_overrides(dict(os.environ if environ is None else environ)))
    _apply_overrides(data, overrides)
    _derive(data)
    validate(data)
    return Config(_to_section(data))


def canonical_json(obj: Any) -> str:
    return json.dumps(_to_plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_hash(cfg: dict) -> str:
    """SHA-256 of the canonical JSON of the merged config."""
    return "sha256:" + hashlib.sha256(canonical_json(cfg).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def resolve_path(p: str | Path) -> Path:
    """Resolve a config path relative to the repository root."""
    p = Path(p)
    return p if p.is_absolute() else ROOT / p

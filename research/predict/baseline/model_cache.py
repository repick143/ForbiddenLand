"""On-disk XGBoost model cache for the prediction baseline.

The model file keeps a fingerprint of the training configuration plus the validation-selected
threshold as Booster attributes. ``load_cached`` enforces the fingerprint (used by the training and
combined CLIs); ``load_model_file`` loads any usable Booster for prediction without enforcing a
matching training configuration, because scoring a different universe or window is legitimate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import xgboost as xgb

_CACHE_ATTR = "predict_baseline_cache_v1"


class CacheLoad:
    """Outcome of attempting to load a cached Booster."""

    __slots__ = ("fingerprint", "model", "params", "reason", "threshold")

    def __init__(
        self,
        model: xgb.Booster | None,
        params: dict[str, Any] | None,
        reason: str | None,
        fingerprint: str | None = None,
        threshold: float | None = None,
    ) -> None:
        self.model = model
        self.params = params
        self.reason = reason
        self.fingerprint = fingerprint
        self.threshold = threshold


def build_fingerprint(
    symbols: tuple[str, ...],
    start_date: str,
    end_date: str,
    adjust: str,
    train_end: str,
    valid_end: str,
    seed: int,
    features: list[str],
    default_params: dict[str, Any],
) -> str:
    """SHA-256 over the training configuration that must match for a cache hit."""

    payload = {
        "symbols": sorted(symbols),
        "start_date": start_date,
        "end_date": end_date,
        "adjust": adjust,
        "train_end": train_end,
        "valid_end": valid_end,
        "seed": seed,
        "features": sorted(features),
        "default_params": default_params,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _meta(booster: xgb.Booster) -> dict[str, Any] | None:
    raw = booster.attributes().get(_CACHE_ATTR)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def save_cached(
    model: xgb.Booster,
    path: Path,
    fingerprint: str,
    params: dict[str, Any],
    *,
    threshold: float | None = None,
) -> None:
    """Attach cache metadata to ``model`` and write it to ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {"fingerprint": fingerprint, "params": params, "threshold": threshold}
    model.set_attr(**{_CACHE_ATTR: json.dumps(metadata)})
    model.save_model(str(path))


def load_cached(path: Path, fingerprint: str) -> CacheLoad:
    """Return the cached Booster when it matches ``fingerprint``.

    Missing, unreadable, corrupt, or configuration-mismatched files return a miss with ``reason``
    so the caller can report why the baseline retrains.
    """

    if not path.is_file():
        return CacheLoad(None, None, "missing")
    try:
        booster = xgb.Booster()
        booster.load_model(str(path))
    except (OSError, ValueError, TypeError, xgb.core.XGBoostError):
        return CacheLoad(None, None, "corrupt")
    meta = _meta(booster)
    if meta is None:
        return CacheLoad(None, None, "missing_metadata")
    if meta.get("fingerprint") != fingerprint:
        return CacheLoad(None, None, "fingerprint_mismatch")
    params = meta.get("params")
    if not isinstance(params, dict):
        return CacheLoad(None, None, "invalid_metadata")
    threshold = meta.get("threshold")
    return CacheLoad(
        booster,
        params,
        None,
        fingerprint,
        threshold if isinstance(threshold, (int, float)) else None,
    )


def load_model_file(path: Path) -> CacheLoad:
    """Load a Booster for prediction without enforcing the training fingerprint.

    Missing or corrupt files return a miss with ``reason``. A usable model returns with the embedded
    fingerprint, parameters, and threshold when present; models saved without cache metadata remain
    usable (metadata fields stay ``None``).
    """

    if not path.is_file():
        return CacheLoad(None, None, "missing")
    try:
        booster = xgb.Booster()
        booster.load_model(str(path))
    except (OSError, ValueError, TypeError, xgb.core.XGBoostError):
        return CacheLoad(None, None, "corrupt")
    meta = _meta(booster)
    if meta is None:
        return CacheLoad(booster, None, None, None, None)
    params = meta.get("params")
    threshold = meta.get("threshold")
    return CacheLoad(
        booster,
        params if isinstance(params, dict) else None,
        None,
        meta.get("fingerprint"),
        threshold if isinstance(threshold, (int, float)) else None,
    )

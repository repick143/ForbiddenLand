from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb

from research.predict.baseline.model_cache import (
    build_fingerprint,
    load_cached,
    load_model_file,
    save_cached,
)


def _fingerprint(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "symbols": ("600183",),
        "start_date": "20190101",
        "end_date": "20261016",
        "adjust": "qfq",
        "train_end": "20231231",
        "valid_end": "20251231",
        "seed": 42,
        "features": ["a", "b"],
        "default_params": {"max_depth": 6},
    }
    base.update(overrides)
    return build_fingerprint(**base)


def _booster(seed: int = 1) -> xgb.Booster:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(120, 4))
    labels = (matrix[:, 0] > 0).astype(int)
    return xgb.train(
        {"objective": "binary:logistic", "seed": seed},
        xgb.DMatrix(matrix, label=labels),
        num_boost_round=10,
    )


def test_fingerprint_is_stable_and_sensitive_to_configuration() -> None:
    assert _fingerprint() == _fingerprint()
    baseline = _fingerprint()
    assert baseline != _fingerprint(symbols=("600183", "688256"))
    assert baseline != _fingerprint(start_date="20200101")
    assert baseline != _fingerprint(end_date="20261101")
    assert baseline != _fingerprint(adjust="hfq")
    assert baseline != _fingerprint(train_end="20240101")
    assert baseline != _fingerprint(valid_end="20261101")
    assert baseline != _fingerprint(seed=7)
    assert baseline != _fingerprint(features=["a"])
    assert baseline != _fingerprint(default_params={"max_depth": 8})


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "model.ubj"
    fingerprint = _fingerprint()
    params = {"objective": "binary:logistic", "seed": 1}
    model = _booster()
    save_cached(model, path, fingerprint, params, threshold=0.63)
    assert path.is_file()

    loaded = load_cached(path, fingerprint)
    assert loaded.model is not None
    assert loaded.reason is None
    assert loaded.params == params
    assert loaded.fingerprint == fingerprint
    assert loaded.threshold == 0.63
    dmatrix = xgb.DMatrix(np.zeros((5, 4)))
    assert np.allclose(loaded.model.predict(dmatrix), model.predict(dmatrix))


def test_load_model_file_returns_embedded_metadata(tmp_path: Path) -> None:
    path = tmp_path / "model.ubj"
    fingerprint = _fingerprint()
    save_cached(_booster(), path, fingerprint, {"seed": 1}, threshold=0.61)
    loaded = load_model_file(path)
    assert loaded.model is not None
    assert loaded.reason is None
    assert loaded.fingerprint == fingerprint
    assert loaded.params == {"seed": 1}
    assert loaded.threshold == 0.61


def test_load_model_file_accepts_model_without_metadata(tmp_path: Path) -> None:
    path = tmp_path / "plain.ubj"
    _booster().save_model(str(path))
    loaded = load_model_file(path)
    assert loaded.model is not None
    assert loaded.reason is None
    assert loaded.fingerprint is None
    assert loaded.params is None
    assert loaded.threshold is None


def test_load_model_file_missing_and_corrupt(tmp_path: Path) -> None:
    assert load_model_file(tmp_path / "absent.ubj").reason == "missing"
    corrupt = tmp_path / "corrupt.ubj"
    corrupt.write_bytes(b"not an xgboost model")
    loaded = load_model_file(corrupt)
    assert loaded.model is None
    assert loaded.reason == "corrupt"


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "models" / "model.ubj"
    save_cached(_booster(), path, _fingerprint(), {"seed": 1})
    assert path.is_file()


def test_load_missing_file_returns_miss(tmp_path: Path) -> None:
    loaded = load_cached(tmp_path / "absent.ubj", _fingerprint())
    assert loaded.model is None
    assert loaded.reason == "missing"


def test_load_fingerprint_mismatch_returns_miss(tmp_path: Path) -> None:
    path = tmp_path / "model.ubj"
    save_cached(_booster(), path, _fingerprint(), {"seed": 1})
    loaded = load_cached(path, _fingerprint(seed=7))
    assert loaded.model is None
    assert loaded.reason == "fingerprint_mismatch"


def test_load_corrupt_file_returns_miss(tmp_path: Path) -> None:
    path = tmp_path / "model.ubj"
    path.write_bytes(b"not an xgboost model")
    loaded = load_cached(path, _fingerprint())
    assert loaded.model is None
    assert loaded.reason == "corrupt"

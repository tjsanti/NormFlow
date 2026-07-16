"""Tests for NormFlow's managed local embedding-model contract."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from normflow.embedding_model import (
    EMBEDDING_MODEL_BUNDLE,
    EMBEDDING_MODEL_IDENTITY,
    EMBEDDING_MODEL_MANIFEST,
    EMBEDDING_MODEL_REPOSITORY,
    EMBEDDING_MODEL_REVISION,
    EmbeddingModelUnavailableError,
    load_embedding_model,
    managed_embedding_model_path,
)
from normflow.semantic_index import _ensure_model


def test_embedding_model_has_one_immutable_bundle_identity():
    assert EMBEDDING_MODEL_REPOSITORY == (
        "sentence-transformers/all-MiniLM-L6-v2"
    )
    assert EMBEDDING_MODEL_REVISION == (
        "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    )
    assert EMBEDDING_MODEL_IDENTITY == (
        "sentence-transformers/all-MiniLM-L6-v2@"
        "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    )


def test_embedding_model_loads_only_the_valid_managed_local_bundle(tmp_path: Path):
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / EMBEDDING_MODEL_MANIFEST).write_text(
        json.dumps({
            "repository": EMBEDDING_MODEL_REPOSITORY,
            "revision": EMBEDDING_MODEL_REVISION,
            "identity": EMBEDDING_MODEL_IDENTITY,
        }),
        encoding="utf-8",
    )
    model = object()
    factory = Mock(return_value=model)

    loaded = load_embedding_model(model_path, model_factory=factory)

    assert loaded is model
    factory.assert_called_once_with(
        str(model_path), local_files_only=True, device="cpu"
    )
    assert managed_embedding_model_path(tmp_path) == (
        tmp_path / "share" / "normflow" / "models" / EMBEDDING_MODEL_BUNDLE
    )


def test_missing_embedding_model_reports_the_required_local_path(tmp_path: Path):
    missing_path = tmp_path / "missing-model"
    factory = Mock()

    with pytest.raises(EmbeddingModelUnavailableError) as raised:
        load_embedding_model(missing_path, model_factory=factory)

    message = str(raised.value)
    assert str(missing_path) in message
    assert "Reinstall NormFlow" in message
    assert "local embedding model" in message
    factory.assert_not_called()


def test_invalid_embedding_model_identity_is_rejected_before_loading(tmp_path: Path):
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / EMBEDDING_MODEL_MANIFEST).write_text(
        json.dumps({
            "repository": EMBEDDING_MODEL_REPOSITORY,
            "revision": "different-revision",
            "identity": EMBEDDING_MODEL_IDENTITY,
        }),
        encoding="utf-8",
    )
    factory = Mock()

    with pytest.raises(EmbeddingModelUnavailableError, match="missing or invalid"):
        load_embedding_model(model_path, model_factory=factory)

    factory.assert_not_called()


def test_semantic_index_uses_the_managed_embedding_model_loader():
    model = object()
    _ensure_model.cache_clear()

    with patch("normflow.semantic_index.load_embedding_model", return_value=model) as load:
        assert _ensure_model() is model
        assert _ensure_model() is model

    load.assert_called_once_with()
    _ensure_model.cache_clear()

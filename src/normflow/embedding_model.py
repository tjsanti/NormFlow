"""Identity and local loading boundary for NormFlow's embedding model."""

import json
from pathlib import Path
import sys
from typing import Protocol


EMBEDDING_MODEL_REPOSITORY = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
EMBEDDING_MODEL_IDENTITY = (
    f"{EMBEDDING_MODEL_REPOSITORY}@{EMBEDDING_MODEL_REVISION}"
)
EMBEDDING_MODEL_BUNDLE = f"all-MiniLM-L6-v2-{EMBEDDING_MODEL_REVISION}"
EMBEDDING_MODEL_MANIFEST = "normflow-model.json"


class EmbeddingModelUnavailableError(RuntimeError):
    """The required managed embedding-model bundle cannot be used."""


class EmbeddingModel(Protocol):
    """The model operations required by semantic indexing."""

    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool,
    ) -> object: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class EmbeddingModelFactory(Protocol):
    """Construct an embedding model from a verified local bundle."""

    def __call__(
        self,
        model_path: str,
        *,
        local_files_only: bool,
    ) -> EmbeddingModel: ...


def managed_embedding_model_path(prefix: str | Path | None = None) -> Path:
    """Return the pinned model location inside NormFlow's managed runtime."""
    runtime_prefix = Path(sys.prefix) if prefix is None else Path(prefix)
    return (
        runtime_prefix
        / "share"
        / "normflow"
        / "models"
        / EMBEDDING_MODEL_BUNDLE
    )


def load_embedding_model(
    model_path: str | Path | None = None,
    *,
    model_factory: EmbeddingModelFactory | None = None,
) -> EmbeddingModel:
    """Load the exact managed model bundle without Hugging Face network access."""
    path = (
        managed_embedding_model_path()
        if model_path is None
        else Path(model_path).expanduser().resolve()
    )
    error_message = (
        f"NormFlow's required local embedding model is missing or invalid at {path}. "
        "Reinstall NormFlow to restore the pinned model bundle."
    )
    try:
        manifest = json.loads(
            (path / EMBEDDING_MODEL_MANIFEST).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise EmbeddingModelUnavailableError(error_message) from error
    expected = {
        "repository": EMBEDDING_MODEL_REPOSITORY,
        "revision": EMBEDDING_MODEL_REVISION,
        "identity": EMBEDDING_MODEL_IDENTITY,
    }
    if not isinstance(manifest, dict) or any(
        manifest.get(key) != value for key, value in expected.items()
    ):
        raise EmbeddingModelUnavailableError(error_message)

    try:
        if model_factory is None:
            from sentence_transformers import SentenceTransformer

            model_factory = SentenceTransformer
        return model_factory(str(path), local_files_only=True)
    except Exception as error:
        raise EmbeddingModelUnavailableError(error_message) from error

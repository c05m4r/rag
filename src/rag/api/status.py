from __future__ import annotations

import json
import logging
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from langchain_core.documents import Document
from qdrant_client import QdrantClient

from rag.bootstrap import AppState
from rag.config import settings
from rag.ingestion.loader import load_markdown_documents
from rag.vectorstore.traceability import TraceabilityStore
from rag.vectorstore.turbovec_store import VectorStoreManager

logger = logging.getLogger(__name__)


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _configured_embedding_model() -> str:
    if settings.embeddings_provider == "openai":
        return settings.embeddings_model_openai
    if settings.embeddings_provider == "gemini":
        return settings.embeddings_model_gemini
    if settings.embeddings_provider == "ollama":
        return settings.embeddings_model_ollama
    return settings.embeddings_model_hf


def _configured_llm_model() -> str:
    if settings.llm_provider == "openai":
        return settings.llm_model_openai
    if settings.llm_provider == "gemini":
        return settings.llm_model_gemini
    if settings.llm_provider == "ollama":
        return settings.llm_model_ollama
    return settings.llm_model_hf


def _load_current_sources() -> tuple[dict[str, str], list[Document]]:
    docs = load_markdown_documents(settings.rag_root_path)
    current: dict[str, str] = {}
    for doc in docs:
        source = str(doc.metadata.get("source", "")).strip()
        checksum = str(doc.metadata.get("checksum", "")).strip()
        if source:
            current[source] = checksum
    return current, docs


def _check_ollama_models(base_url: str) -> tuple[bool, list[str], str | None]:
    try:
        tags_url = urljoin(base_url.rstrip("/") + "/", "api/tags")
        request = Request(tags_url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = [
            entry.get("name", "")
            for entry in payload.get("models", [])
            if entry.get("name")
        ]
        return True, sorted(models), None
    except Exception as exc:
        return False, [], _safe_error(exc)


def _provider_availability(provider: str, model: str) -> dict:
    if provider == "ollama":
        _, _, error = _check_ollama_models(settings.llm_ollama_base_url)
        return {
            "provider": provider,
            "model": model,
            "endpoint": settings.llm_ollama_base_url,
            "error": error,
        }

    if provider == "openai":
        has_key = bool(settings.openai_api_key)
        return {
            "provider": provider,
            "model": model,
            "endpoint": None,
            "error": None if has_key else "OPENAI_API_KEY is not configured",
        }

    if provider == "gemini":
        has_key = bool(settings.google_api_key)
        return {
            "provider": provider,
            "model": model,
            "endpoint": None,
            "error": None if has_key else "GOOGLE_API_KEY is not configured",
        }

    has_token = bool(settings.huggingface_api_token)
    return {
        "provider": provider,
        "model": model,
        "endpoint": None,
        "error": None if has_token else "HUGGINGFACE_API_TOKEN is not configured",
    }


def _vector_store_details() -> dict:
    trace = TraceabilityStore(settings.traceability_db_path)
    manager = VectorStoreManager(
        state_dir=settings.vector_state_dir,
        turbovec_dir=settings.turbovec_store_dir,
    )

    _, docs = _load_current_sources()
    coverage = trace.coverage_from_current_docs(docs)
    current_fp = manager.compute_corpus_fingerprint(docs)
    stored_fp, stored_algorithm = manager.read_fingerprint_state()
    latest_run = trace.get_latest_run()

    details: dict = {
        "provider": settings.vector_store_provider,
        "traceability": {
            "db_path": str(settings.traceability_db_path),
            "latest_run": latest_run,
        },
        "fingerprint": {
            "current": current_fp,
            "current_algorithm": settings.hash_algorithm,
            "stored": stored_fp,
            "stored_algorithm": stored_algorithm,
            "matches": bool(stored_fp)
            and (stored_fp == current_fp)
            and (stored_algorithm == settings.hash_algorithm),
        },
        "coverage": coverage,
    }

    if settings.vector_store_provider == "qdrant":
        collection_exists = False
        points_count = 0
        error = None
        try:
            client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key,
                prefer_grpc=settings.qdrant_prefer_grpc,
            )
            collections = client.get_collections().collections
            collection_exists = any(
                c.name == settings.qdrant_collection_name for c in collections
            )
            if collection_exists:
                info = client.get_collection(settings.qdrant_collection_name)
                points_count = int(info.points_count or 0)
        except Exception as exc:
            error = _safe_error(exc)
            logger.warning("Failed to collect qdrant status: %s", error)

        details["qdrant"] = {
            "url": settings.qdrant_url,
            "collection_name": settings.qdrant_collection_name,
            "collection_exists": collection_exists,
            "points_count": points_count,
            "error": error,
        }
        return details

    index_path = settings.turbovec_store_dir / "index.tvim"
    docstore_path = settings.turbovec_store_dir / "docstore.json"
    details["turbovec"] = {
        "index_path": str(index_path),
        "docstore_path": str(docstore_path),
        "index_exists": index_path.exists(),
        "docstore_exists": docstore_path.exists(),
    }
    return details


def _runtime_payload(state: AppState) -> dict:
    return {
        "index_ready": state.retriever is not None,
        "chunks_indexed": state.chunks_indexed,
        "requires_ingest": state.retriever is None,
    }


def _models_payload(llm_model: str, embeddings_model: str) -> dict:
    llm = _provider_availability(settings.llm_provider, llm_model)
    embeddings = _provider_availability(settings.embeddings_provider, embeddings_model)

    return {
        "llm_provider": llm["provider"],
        "llm_model": llm["model"],
        "llm_endpoint": llm["endpoint"],
        "llm_error": llm["error"],
        "embeddings_provider": embeddings["provider"],
        "embeddings_model": embeddings["model"],
        "embeddings_endpoint": embeddings["endpoint"],
        "embeddings_error": embeddings["error"],
    }


def build_config_payload(state: AppState) -> dict:
    llm_model = _configured_llm_model()
    embeddings_model = _configured_embedding_model()

    return {
        "configuration": {
            "app_name": settings.app_name,
            "log_level": settings.log_level,
            "rag_root_path": str(settings.rag_root_path),
            "vector_state_dir": str(settings.vector_state_dir),
            "vector_store_provider": settings.vector_store_provider,
            "hash_algorithm": settings.hash_algorithm,
            "retrieval_k": settings.retrieval_k,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "force_reindex": settings.force_reindex,
            "llm_provider": settings.llm_provider,
            "llm_model": llm_model,
            "embeddings_provider": settings.embeddings_provider,
            "embeddings_model": embeddings_model,
        },
        "models": _models_payload(llm_model, embeddings_model),
    }


def build_traceability_payload(page: int = 1, page_size: int = 10) -> dict:
    trace = TraceabilityStore(settings.traceability_db_path)
    return trace.list_runs(page=page, page_size=page_size)


def build_traceability_files_payload(page: int = 1, page_size: int = 10) -> dict:
    trace = TraceabilityStore(settings.traceability_db_path)
    current, _ = _load_current_sources()
    return trace.get_files_status(current, page=page, page_size=page_size)


def build_traceability_run_files_payload(
    run_id: int, page: int = 1, page_size: int = 10
) -> dict:
    trace = TraceabilityStore(settings.traceability_db_path)
    current, _ = _load_current_sources()
    return trace.get_files_status(
        current, page=page, page_size=page_size, run_id=run_id
    )


def build_health_status() -> dict:
    store = _vector_store_details()
    provider = store["provider"]

    if provider == "qdrant":
        qdrant = store["qdrant"]
        if qdrant["error"]:
            return {
                "status": "error",
                "detail": f"Qdrant is unavailable: {qdrant['error']}",
            }
        return {"status": "ok"}

    turbovec = store["turbovec"]
    missing_files: list[str] = []
    if not turbovec["index_exists"]:
        missing_files.append("index.tvim")
    if not turbovec["docstore_exists"]:
        missing_files.append("docstore.json")

    if missing_files:
        missing = ", ".join(missing_files)
        return {
            "status": "error",
            "detail": f"TurboVec is unavailable: missing {missing}",
        }

    return {"status": "ok"}


def build_status(state: AppState) -> dict:
    config_payload = build_config_payload(state)
    return {
        "status": "ok",
        "runtime": _runtime_payload(state),
        "models": config_payload["models"],
        "endpoints": {
            "config": "/config",
            "traceability": "/ingest/traceability",
        },
        "summary": {
            "vector_store_provider": config_payload["configuration"][
                "vector_store_provider"
            ],
        },
    }

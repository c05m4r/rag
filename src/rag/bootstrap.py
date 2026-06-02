from __future__ import annotations

import logging

from rag.config import settings
from rag.embeddings.factory import build_embeddings
from rag.ingestion.loader import load_markdown_documents
from rag.ingestion.splitter import split_documents
from rag.vectorstore.query_traceability_store import QueryTraceabilityStore
from rag.vectorstore.metadata_store import MetadataStore
from rag.vectorstore.traceability import TraceabilityStore
from rag.vectorstore.turbovec_store import VectorStoreManager

logger = logging.getLogger(__name__)


class AppState:
    def __init__(
        self,
        llm,
        prompt,
        retriever=None,
        chunks_indexed: int = 0,
        traceability_store: TraceabilityStore | None = None,
        query_traceability_store: QueryTraceabilityStore | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.prompt = prompt
        self.chunks_indexed = chunks_indexed
        self.traceability_store = traceability_store
        self.query_traceability_store = query_traceability_store


def initialize_runtime(llm, prompt) -> AppState:
    trace = TraceabilityStore(settings.traceability_db_path)
    query_trace = QueryTraceabilityStore(settings.traceability_db_path)
    return AppState(
        llm=llm,
        prompt=prompt,
        traceability_store=trace,
        query_traceability_store=query_trace,
    )


def initialize_rag(
    llm,
    prompt,
    force_reindex: bool = False,
    traceability_store: TraceabilityStore | None = None,
    query_traceability_store: QueryTraceabilityStore | None = None,
) -> AppState:
    docs = load_markdown_documents(settings.rag_root_path)
    chunks = split_documents(docs, settings.chunk_size, settings.chunk_overlap)

    embeddings = build_embeddings()
    manager = VectorStoreManager(
        state_dir=settings.vector_state_dir,
        turbovec_dir=settings.turbovec_store_dir,
    )
    trace = traceability_store or TraceabilityStore(settings.traceability_db_path)

    run_id = trace.start_ingestion_run(
        embedding_model=(
            settings.embeddings_model_openai
            if settings.embeddings_provider == "openai"
            else settings.embeddings_model_gemini
            if settings.embeddings_provider == "gemini"
            else settings.embeddings_model_ollama
            if settings.embeddings_provider == "ollama"
            else settings.embeddings_model_hf
        ),
    )

    try:
        store = manager.load_or_build(
            embeddings,
            chunks,
            force_reindex=force_reindex,
            allow_auto_reindex=force_reindex,
        )
        trace.persist_indexed_state(
            run_id=run_id,
            docs=docs,
            chunks=chunks,
            rebuild_triggered=manager.last_rebuild_triggered,
        )
        trace.complete_ingestion_run(run_id, success=True)
    except Exception as exc:
        trace.complete_ingestion_run(
            run_id, success=False, error_message=f"{type(exc).__name__}: {exc}"
        )
        raise

    metadata_store = MetadataStore(settings.vector_state_dir / "metadata_snapshot.json")
    metadata_store.save(chunks)

    retriever = store.as_retriever(search_kwargs={"k": settings.retrieval_k})
    logger.info("RAG initialized with %s chunks", len(chunks))
    return AppState(
        llm=llm,
        prompt=prompt,
        retriever=retriever,
        chunks_indexed=len(chunks),
        traceability_store=trace,
        query_traceability_store=(
            query_traceability_store
            or QueryTraceabilityStore(settings.traceability_db_path)
        ),
    )

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from rag.api.schemas import (
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    SourceChunk,
)
from rag.api.status import (
    build_config_payload,
    build_health_status,
    build_traceability_files_payload,
    build_traceability_payload,
    build_traceability_run_files_payload,
)
from rag.bootstrap import AppState, initialize_rag, initialize_runtime
from rag.config import settings
from rag.integrations.telegram_client import TelegramClient
from rag.integrations.telegram_webhook_guard import telegram_webhook_guard
from rag.logging_config import configure_logging
from rag.rag.pipeline import build_llm, build_prompt, format_context

configure_logging()
logger = logging.getLogger(__name__)

_state: AppState | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _state
    llm = build_llm()
    prompt = build_prompt()
    _state = initialize_runtime(llm=llm, prompt=prompt)

    try:
        logger.info("Auto-indexing RAG on startup...")
        _state = initialize_rag(
            llm=llm,
            prompt=prompt,
            traceability_store=_state.traceability_store,
            query_traceability_store=_state.query_traceability_store,
        )
        logger.info("Auto-indexing complete: %s chunks", _state.chunks_indexed)
    except Exception as exc:
        logger.warning("Auto-indexing on startup failed: %s", exc)

    if settings.telegram_bot_token and settings.telegram_webhook_url:
        try:
            client = TelegramClient(settings.telegram_bot_token)
            client.set_webhook(
                webhook_url=settings.telegram_webhook_url,
                secret_token=settings.telegram_webhook_secret,
            )
            logger.info(
                "Telegram webhook registered on startup: %s",
                settings.telegram_webhook_url,
            )
        except RuntimeError as exc:
            logger.warning("Telegram webhook auto-registration failed: %s", exc)

    yield


app = FastAPI(title="rag", version="1.0.0", lifespan=lifespan)

security = HTTPBearer(auto_error=False)


@app.post("/telegram/webhook", include_in_schema=False)
def telegram_webhook(
    update: dict,
    telegram_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> dict:
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="Telegram is not configured")

    expected_secret = settings.telegram_webhook_secret
    if expected_secret and telegram_secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid Telegram secret token")

    update_id = update.get("update_id")
    if isinstance(update_id, int) and telegram_webhook_guard.is_duplicate_update(update_id):
        logger.info("Ignoring duplicate Telegram update_id=%s", update_id)
        return {"ok": True, "detail": "ignored_duplicate"}

    message = update.get("message")
    if not isinstance(message, dict):
        return {"ok": True, "detail": "ignored"}

    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str) or not text.strip():
        return {"ok": True, "detail": "ignored"}

    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return {"ok": True, "detail": "ignored"}

    reply_to_message_id = message.get("message_id")
    if not isinstance(reply_to_message_id, int):
        reply_to_message_id = None

    client = TelegramClient(settings.telegram_bot_token)
    normalized_text = text.strip().lower()
    if normalized_text in {"/start", "start", "start/"} or normalized_text.startswith(
        "/start@"
    ):
        if not telegram_webhook_guard.is_first_start(chat_id):
            return {"ok": True, "detail": "start_already_initialized"}

        try:
            client.send_message(
                chat_id=chat_id,
                text="Hola, soy tu asistente RAG. Enviame una pregunta y te respondo con el contexto indexado. \n\nPD: Aveces alucino cosas, valida la información de las fuentes",
                reply_to_message_id=reply_to_message_id,
            )
        except RuntimeError as exc:
            logger.error("Failed to send Telegram start message: %s", exc)
        return {"ok": True}

    try:
        response = _answer_question(text.strip(), interaction_type="telegram")
    except HTTPException as exc:
        if exc.status_code == 503:
            try:
                client.send_message(
                    chat_id=chat_id,
                    text="Todavía no tengo el índice listo.",
                    reply_to_message_id=reply_to_message_id,
                )
            except RuntimeError as exc:
                logger.error("Failed to send Telegram retry message: %s", exc)
            return {"ok": True}
        raise

    try:
        client.send_message(
            chat_id=chat_id,
            text=response.answer,
            reply_to_message_id=reply_to_message_id,
        )
    except RuntimeError as exc:
        logger.error("Failed to send Telegram answer: %s", exc)
    return {"ok": True}


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = credentials.credentials.strip()
    if not settings.api_key or token != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API token")


def _answer_question(question: str, interaction_type: str = "query") -> QueryResponse:
    if _state is None:
        raise HTTPException(status_code=503, detail="RAG not ready")
    if _state.retriever is None:
        raise HTTPException(
            status_code=503, detail="Index is not ready. Run POST /ingest first"
        )

    docs = _state.retriever.invoke(question)
    context = format_context(docs)

    queried_at = datetime.now(UTC).isoformat()
    chain = _state.prompt | _state.llm
    result = chain.invoke({"context": context, "question": question})
    answer = getattr(result, "content", str(result))

    sources = [
        SourceChunk(
            source=str(doc.metadata.get("source", "unknown")),
            preview=doc.page_content[:220],
        )
        for doc in docs
    ]
    response = QueryResponse(answer=answer, sources=sources)

    if _state.query_traceability_store is not None:
        try:
            _state.query_traceability_store.record_interaction(
                interaction_type=interaction_type,
                question=question,
                answer=answer,
                queried_at=queried_at,
                sources=[
                    {"source": source.source, "preview": source.preview}
                    for source in sources
                ],
            )
        except Exception:
            logger.exception("Failed to persist query interaction in ORM store")

    return response


@app.get("/status", dependencies=[Depends(verify_api_key)])
def status() -> dict:
    if _state is None:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": "Application state not initialized"},
        )

    payload = build_health_status()
    if payload["status"] != "ok":
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/config", dependencies=[Depends(verify_api_key)])
def config() -> dict:
    if _state is None:
        raise HTTPException(status_code=503, detail="Application state not initialized")
    return build_config_payload(_state)


@app.get("/ingest/traceability", dependencies=[Depends(verify_api_key)])
def traceability(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict:
    if _state is None:
        raise HTTPException(status_code=503, detail="Application state not initialized")
    return build_traceability_payload(page=page, page_size=page_size)


@app.get("/ingest/traceability/{id_run}/files", dependencies=[Depends(verify_api_key)])
def traceability_run_files(
    id_run: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict:
    if _state is None:
        raise HTTPException(status_code=503, detail="Application state not initialized")
    payload = build_traceability_run_files_payload(
        id_run, page=page, page_size=page_size
    )
    if payload["run"] is None:
        raise HTTPException(
            status_code=404, detail=f"Ingestion run not found: {id_run}"
        )
    if int(payload.get("run_files_count", 0)) == 0:
        raise HTTPException(
            status_code=404, detail=f"Ingestion run has no associated files: {id_run}"
        )
    return payload


@app.get("/query/traceability", dependencies=[Depends(verify_api_key)])
def query_traceability(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    interaction_type: str | None = Query(default=None, min_length=1, max_length=50),
) -> dict:
    if _state is None:
        raise HTTPException(status_code=503, detail="Application state not initialized")
    if _state.query_traceability_store is None:
        raise HTTPException(
            status_code=503, detail="Query traceability store not ready"
        )
    try:
        return _state.query_traceability_store.list_interactions(
            page=page,
            page_size=page_size,
            interaction_type=interaction_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(verify_api_key)])
def query(payload: QueryRequest) -> QueryResponse:
    try:
        return _answer_question(payload.question, interaction_type="query")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Query processing failed")
        return JSONResponse(status_code=500, content={"detail": f"Query failed: {exc}"})


@app.post("/ingest", response_model=IngestResponse, dependencies=[Depends(verify_api_key)])
def ingest(payload: IngestRequest) -> IngestResponse:
    global _state
    if _state is None:
        _state = initialize_runtime(llm=build_llm(), prompt=build_prompt())

    try:
        _state = initialize_rag(
            llm=_state.llm,
            prompt=_state.prompt,
            force_reindex=payload.force_reindex,
            traceability_store=_state.traceability_store,
            query_traceability_store=_state.query_traceability_store,
        )
        return IngestResponse(status="ok", chunks_indexed=_state.chunks_indexed)
    except Exception as exc:
        logger.exception("Ingest failed")
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}")

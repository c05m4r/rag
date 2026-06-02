from __future__ import annotations

import logging

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownTextSplitter,
    RecursiveCharacterTextSplitter,
)

logger = logging.getLogger(__name__)


def split_documents(
    documents: list[Document], chunk_size: int, chunk_overlap: int
) -> list[Document]:
    """Split markdown documents with markdown-aware splitter and fallback."""
    if not documents:
        return []

    try:
        splitter = MarkdownTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        chunks = splitter.split_documents(documents)
    except Exception:
        logger.exception(
            "MarkdownTextSplitter failed; falling back to RecursiveCharacterTextSplitter"
        )
        fallback = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        chunks = fallback.split_documents(documents)

    for idx, chunk in enumerate(chunks):
        chunk.metadata["chunk_id"] = idx

    logger.info("Chunked %s documents into %s chunks", len(documents), len(chunks))
    return chunks

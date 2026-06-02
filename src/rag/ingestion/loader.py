from __future__ import annotations

import logging
import re
from pathlib import Path

from langchain_core.documents import Document

from rag.config import settings
from rag.hashing import hash_text

logger = logging.getLogger(__name__)


def clean_markdown_text(text: str) -> str:
    """Apply basic content cleanup while preserving markdown semantics."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def compute_content_hash(content: str) -> str:
    return hash_text(content, settings.hash_algorithm)


def scan_markdown_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"RAG directory does not exist: {root}")
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def load_markdown_documents(root: Path) -> list[Document]:
    files = scan_markdown_files(root)
    logger.info("Discovered %s markdown files under %s", len(files), root)

    documents: list[Document] = []
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
            cleaned = clean_markdown_text(raw)
            if not cleaned:
                logger.warning("Skipping empty markdown file: %s", path)
                continue

            checksum = compute_content_hash(cleaned)
            stat = path.stat()
            doc = Document(
                page_content=cleaned,
                metadata={
                    "source": str(path),
                    "filename": path.name,
                    "checksum": checksum,
                    "hash_algorithm": settings.hash_algorithm,
                    "mtime": int(stat.st_mtime),
                },
            )
            documents.append(doc)
        except UnicodeDecodeError:
            logger.exception("Invalid UTF-8 content in file: %s", path)
        except Exception:
            logger.exception("Failed to process markdown file: %s", path)

    logger.info("Loaded %s markdown documents", len(documents))
    return documents

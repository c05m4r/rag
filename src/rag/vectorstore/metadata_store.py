from __future__ import annotations

import json
from pathlib import Path

from langchain_core.documents import Document


class MetadataStore:
    """Simple sidecar metadata dump for observability and troubleshooting."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, documents: list[Document]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [doc.metadata for doc in documents]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
        )

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

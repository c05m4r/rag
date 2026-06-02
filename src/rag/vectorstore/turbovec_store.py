from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
from turbovec.langchain import TurboQuantVectorStore

from rag.config import settings
from rag.hashing import HashAlgorithm, hash_text, normalize_hash_algorithm

logger = logging.getLogger(__name__)


class VectorStoreManager:
    def __init__(self, state_dir: Path, turbovec_dir: Path) -> None:
        self.state_dir = state_dir
        self.turbovec_dir = turbovec_dir
        self.fingerprint_file = self.state_dir / "fingerprint.json"
        self.last_rebuild_triggered = False
        self.last_current_fingerprint: str | None = None
        self.last_stored_fingerprint: str | None = None

    def compute_corpus_fingerprint(self, docs: list[Document]) -> str:
        payload = sorted(
            f"{d.metadata.get('source', '')}|{d.metadata.get('checksum', '')}|{d.metadata.get('mtime', '')}"
            for d in docs
        )
        joined = "||".join(payload)
        return hash_text(joined, settings.hash_algorithm)

    def _fingerprint_matches(
        self,
        current_fingerprint: str,
        stored_fingerprint: str | None,
        stored_algorithm: str | None,
    ) -> bool:
        return (
            stored_fingerprint == current_fingerprint
            and stored_algorithm == settings.hash_algorithm
        )

    def read_fingerprint_state(self) -> tuple[str | None, HashAlgorithm | None]:
        if not self.fingerprint_file.exists():
            return None, None
        try:
            data = json.loads(self.fingerprint_file.read_text(encoding="utf-8"))
            fingerprint = str(data.get("fingerprint") or "").strip() or None
            algorithm_raw = data.get("algorithm")
            algorithm = (
                normalize_hash_algorithm(str(algorithm_raw)) if algorithm_raw else None
            )
            return fingerprint, algorithm
        except Exception:
            logger.exception("Failed reading fingerprint file")
            return None, None

    def read_fingerprint(self) -> str | None:
        fingerprint, _ = self.read_fingerprint_state()
        return fingerprint

    def read_fingerprint_algorithm(self) -> HashAlgorithm | None:
        _, algorithm = self.read_fingerprint_state()
        return algorithm

    def write_fingerprint(self, fingerprint: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.fingerprint_file.write_text(
            json.dumps(
                {"fingerprint": fingerprint, "algorithm": settings.hash_algorithm},
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load_or_build(
        self,
        embedding,
        docs: list[Document],
        force_reindex: bool = False,
        allow_auto_reindex: bool = True,
    ):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        current_fp = self.compute_corpus_fingerprint(docs)
        stored_fp, stored_algorithm = self.read_fingerprint_state()
        self.last_current_fingerprint = current_fp
        self.last_stored_fingerprint = stored_fp

        if settings.vector_store_provider == "qdrant":
            return self._load_or_build_qdrant(
                embedding,
                docs,
                current_fp,
                stored_fp,
                stored_algorithm,
                force_reindex,
                allow_auto_reindex,
            )
        return self._load_or_build_turbovec(
            embedding,
            docs,
            current_fp,
            stored_fp,
            stored_algorithm,
            force_reindex,
            allow_auto_reindex,
        )

    def _qdrant_client(self) -> QdrantClient:
        return QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            prefer_grpc=settings.qdrant_prefer_grpc,
        )

    def _qdrant_collection_exists(self, client: QdrantClient) -> bool:
        collections = client.get_collections().collections
        return any(
            collection.name == settings.qdrant_collection_name
            for collection in collections
        )

    def _load_or_build_qdrant(
        self,
        embedding,
        docs: list[Document],
        current_fp: str,
        stored_fp: str | None,
        stored_algorithm: str | None,
        force_reindex: bool,
        allow_auto_reindex: bool,
    ) -> QdrantVectorStore:
        client = self._qdrant_client()
        collection_exists = self._qdrant_collection_exists(client)
        should_rebuild = force_reindex or (not collection_exists)
        if allow_auto_reindex:
            should_rebuild = (
                should_rebuild
                or settings.force_reindex
                or (
                    not self._fingerprint_matches(
                        current_fp, stored_fp, stored_algorithm
                    )
                )
            )

        if should_rebuild:
            self.last_rebuild_triggered = True
            logger.info(
                "Building Qdrant collection '%s' at %s",
                settings.qdrant_collection_name,
                settings.qdrant_url,
            )
            if collection_exists:
                client.delete_collection(
                    collection_name=settings.qdrant_collection_name
                )

            vector_size = len(embedding.embed_query("dimension probe"))
            client.create_collection(
                collection_name=settings.qdrant_collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size, distance=models.Distance.COSINE
                ),
            )

            store = QdrantVectorStore(
                client=client,
                collection_name=settings.qdrant_collection_name,
                embedding=embedding,
            )
            if docs:
                store.add_documents(docs)
            self.write_fingerprint(current_fp)
            return store

        logger.info(
            "Loading existing Qdrant collection '%s'", settings.qdrant_collection_name
        )
        self.last_rebuild_triggered = False
        return QdrantVectorStore(
            client=client,
            collection_name=settings.qdrant_collection_name,
            embedding=embedding,
        )

    def _turbovec_exists(self) -> bool:
        return (self.turbovec_dir / "index.tvim").exists() and (
            self.turbovec_dir / "docstore.json"
        ).exists()

    def _load_or_build_turbovec(
        self,
        embedding,
        docs: list[Document],
        current_fp: str,
        stored_fp: str | None,
        stored_algorithm: str | None,
        force_reindex: bool,
        allow_auto_reindex: bool,
    ) -> TurboQuantVectorStore:
        self.turbovec_dir.mkdir(parents=True, exist_ok=True)
        should_rebuild = force_reindex or (not self._turbovec_exists())
        if allow_auto_reindex:
            should_rebuild = (
                should_rebuild
                or settings.force_reindex
                or (
                    not self._fingerprint_matches(
                        current_fp, stored_fp, stored_algorithm
                    )
                )
            )

        if should_rebuild:
            self.last_rebuild_triggered = True
            logger.info("Building TurboVec index in %s", self.turbovec_dir)
            store = TurboQuantVectorStore.from_documents(
                documents=docs,
                embedding=embedding,
                bit_width=settings.turbovec_bit_width,
            )
            store.dump(str(self.turbovec_dir))
            self.write_fingerprint(current_fp)
            return store

        logger.info("Loading existing TurboVec index from %s", self.turbovec_dir)
        self.last_rebuild_triggered = False
        return TurboQuantVectorStore.load(str(self.turbovec_dir), embedding=embedding)

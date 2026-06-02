from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.documents import Document
from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    desc,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from rag.config import settings
from rag.hashing import hash_bytes


class Base(DeclarativeBase):
    pass


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    file_statuses: Mapped[list["IngestionFileStatus"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    chunk_traces: Mapped[list["ChunkTrace"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class IndexedFile(Base):
    __tablename__ = "indexed_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    current_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_modified_disk: Mapped[str | None] = mapped_column(String, nullable=True)

    file_statuses: Mapped[list["IngestionFileStatus"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )
    chunk_traces: Mapped[list["ChunkTrace"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


class IngestionFileStatus(Base):
    __tablename__ = "ingestion_file_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("indexed_files.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    file_hash_at_run: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[IngestionRun] = relationship(back_populates="file_statuses")
    file: Mapped[IndexedFile] = relationship(back_populates="file_statuses")


class ChunkTrace(Base):
    __tablename__ = "chunk_trace"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("indexed_files.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    vector_store_id: Mapped[str | None] = mapped_column(String, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_hash: Mapped[str] = mapped_column(String, nullable=False)

    run: Mapped[IngestionRun] = relationship(back_populates="chunk_traces")
    file: Mapped[IndexedFile] = relationship(back_populates="chunk_traces")


class TraceabilityStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path.resolve()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._init_schema()

    @contextmanager
    def _session(self):
        session = self.SessionLocal()
        try:
            session.execute(text("PRAGMA foreign_keys=ON"))
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _needs_migration(self) -> bool:
        try:
            inspector = inspect(self.engine)
            if not inspector.has_table("ingestion_runs"):
                return False
            columns = {
                column["name"] for column in inspector.get_columns("ingestion_runs")
            }
            return "ingestion_id" in columns
        except Exception:
            return False

    def _init_schema(self) -> None:
        if self._needs_migration():
            with self._session() as session:
                session.execute(text("DROP TABLE IF EXISTS chunk_trace"))
                session.execute(text("DROP TABLE IF EXISTS ingestion_file_status"))
                session.execute(text("DROP TABLE IF EXISTS indexed_files"))
                session.execute(text("DROP TABLE IF EXISTS ingestion_runs"))

        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_runs_started_at ON ingestion_runs(started_at DESC)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_runs_status ON ingestion_runs(status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_files_file_path ON indexed_files(file_path)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_file_status_run_id ON ingestion_file_status(run_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_file_status_file_id ON ingestion_file_status(file_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_chunk_trace_run_id ON chunk_trace(run_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_chunk_trace_file_id ON chunk_trace(file_id)"
                )
            )

    def start_ingestion_run(
        self,
        *,
        embedding_model: str,
        embedding_dimensions: int | None = None,
    ) -> int:
        started_at = datetime.now(UTC).isoformat()
        with self._session() as session:
            run = IngestionRun(
                started_at=started_at,
                status="in_progress",
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
            )
            session.add(run)
            session.flush()
            return int(run.id)

    def complete_ingestion_run(
        self, run_id: int, *, success: bool, error_message: str | None = None
    ) -> None:
        with self._session() as session:
            run = session.get(IngestionRun, run_id)
            if run is None:
                return
            run.completed_at = datetime.now(UTC).isoformat()
            run.status = "success" if success else "failed"

    def _upsert_file(
        self, session: Session, source: str, checksum: str | None, mtime: int | None
    ) -> int:
        last_modified_disk = (
            datetime.fromtimestamp(mtime, UTC).isoformat() if mtime else None
        )
        file_size_bytes: int | None = None
        try:
            file_size_bytes = Path(source).stat().st_size
        except Exception:
            pass

        file = session.scalar(
            select(IndexedFile).where(IndexedFile.file_path == source)
        )
        if file is None:
            file = IndexedFile(file_path=source)
            session.add(file)

        file.current_hash = checksum
        file.file_size_bytes = file_size_bytes
        file.last_modified_disk = last_modified_disk
        session.flush()
        return int(file.id)

    def persist_indexed_state(
        self,
        *,
        run_id: int,
        docs: list[Document],
        chunks: list[Document],
        rebuild_triggered: bool,
    ) -> None:
        chunks_by_source: dict[str, list[Document]] = {}
        for chunk in chunks:
            source = str(chunk.metadata.get("source", "")).strip()
            if source:
                chunks_by_source.setdefault(source, []).append(chunk)

        with self._session() as session:
            for doc in docs:
                source = str(doc.metadata.get("source", "")).strip()
                if not source:
                    continue
                checksum = str(doc.metadata.get("checksum", "")).strip() or None
                mtime = int(doc.metadata.get("mtime", 0) or 0) or None
                file_id = self._upsert_file(session, source, checksum, mtime)
                session.add(
                    IngestionFileStatus(
                        run_id=run_id,
                        file_id=file_id,
                        status="completed" if rebuild_triggered else "skipped",
                        file_hash_at_run=checksum,
                    )
                )

                if rebuild_triggered:
                    for idx, chunk in enumerate(chunks_by_source.get(source, [])):
                        payload = f"{source}|{checksum}|{chunk.page_content}".encode(
                            "utf-8"
                        )
                        session.add(
                            ChunkTrace(
                                file_id=file_id,
                                run_id=run_id,
                                vector_store_id=None,
                                chunk_index=idx,
                                token_count=len(chunk.page_content.split()),
                                chunk_hash=hash_bytes(payload, settings.hash_algorithm),
                            )
                        )

    def get_latest_run(self) -> dict | None:
        with self._session() as session:
            run = session.scalar(
                select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(1)
            )
            if run is None:
                return None
            return {
                "id": run.id,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "status": run.status,
                "embedding_model": run.embedding_model,
                "embedding_dimensions": run.embedding_dimensions,
            }

    def get_chunk_count_for_latest_success(self) -> int:
        with self._session() as session:
            run = session.scalar(
                select(IngestionRun)
                .where(IngestionRun.status == "success")
                .order_by(desc(IngestionRun.completed_at))
                .limit(1)
            )
            if run is None:
                return 0
            return int(
                session.scalar(
                    select(func.count(ChunkTrace.id)).where(ChunkTrace.run_id == run.id)
                )
                or 0
            )

    def coverage_from_current_docs(self, docs: list[Document]) -> dict:
        current: dict[str, str] = {}
        for doc in docs:
            source = str(doc.metadata.get("source", "")).strip()
            checksum = str(doc.metadata.get("checksum", "")).strip()
            if source:
                current[source] = checksum

        with self._session() as session:
            latest_success = session.scalar(
                select(IngestionRun)
                .where(IngestionRun.status == "success")
                .order_by(desc(IngestionRun.completed_at))
                .limit(1)
            )
            latest_run_id = latest_success.id if latest_success else None

            files = session.execute(select(IndexedFile)).scalars().all()
            indexed_map: dict[str, dict] = {}
            for file in files:
                status_entry = None
                if latest_run_id is not None:
                    status_entry = session.scalar(
                        select(IngestionFileStatus)
                        .where(
                            IngestionFileStatus.file_id == file.id,
                            IngestionFileStatus.run_id == latest_run_id,
                        )
                        .limit(1)
                    )
                indexed_map[file.file_path] = {
                    "file_path": file.file_path,
                    "current_hash": file.current_hash,
                    "run_status": status_entry.status if status_entry else None,
                    "file_hash_at_run": status_entry.file_hash_at_run
                    if status_entry
                    else None,
                }

        indexed_ok: list[str] = []
        missing: list[str] = []
        stale: list[str] = []

        for source, checksum in current.items():
            row = indexed_map.get(source)
            if row is None:
                missing.append(source)
                continue
            file_hash_at_run = str(row["file_hash_at_run"] or "").strip()
            run_status = str(row["run_status"] or "").strip()
            if run_status == "completed" and file_hash_at_run == checksum:
                indexed_ok.append(source)
            elif file_hash_at_run and file_hash_at_run != checksum:
                stale.append(source)
            else:
                missing.append(source)

        extra_indexed = sorted(
            source for source in indexed_map if source not in current
        )
        return {
            "documents_total": len(current),
            "indexed_documents": len(indexed_ok),
            "pending_documents": len(missing) + len(stale),
            "missing_sources": sorted(missing),
            "stale_sources": sorted(stale),
            "extra_indexed_sources": extra_indexed,
            "chunk_trace_count": self.get_chunk_count_for_latest_success(),
        }

    def list_runs(self, page: int = 1, page_size: int = 10) -> dict:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size
        with self._session() as session:
            total = int(session.scalar(select(func.count(IngestionRun.id))) or 0)
            rows = (
                session.execute(
                    select(IngestionRun)
                    .order_by(desc(IngestionRun.started_at))
                    .offset(offset)
                    .limit(page_size)
                )
                .scalars()
                .all()
            )
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "runs": [
                {
                    "id": row.id,
                    "started_at": row.started_at,
                    "completed_at": row.completed_at,
                    "status": row.status,
                    "embedding_model": row.embedding_model,
                    "embedding_dimensions": row.embedding_dimensions,
                }
                for row in rows
            ],
        }

    def _get_run(self, run_id: int | None = None) -> dict | None:
        with self._session() as session:
            if run_id is None:
                run = session.scalar(
                    select(IngestionRun)
                    .order_by(desc(IngestionRun.started_at))
                    .limit(1)
                )
            else:
                run = session.get(IngestionRun, run_id)
            if run is None:
                return None
            return {
                "id": run.id,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "status": run.status,
                "embedding_model": run.embedding_model,
                "embedding_dimensions": run.embedding_dimensions,
            }

    def get_files_status(
        self,
        current_files: dict[str, str],
        *,
        page: int = 1,
        page_size: int = 10,
        run_id: int | None = None,
    ) -> dict:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        run = self._get_run(run_id)

        if run is None:
            entries = [
                {
                    "file_path": source,
                    "status": "pending",
                    "detail": "Detectado en disco. Aun no se han generado embeddings.",
                    "last_ingested_at": None,
                    "chunks_count": 0,
                }
                for source in sorted(current_files)
            ]
            total = len(entries)
            start = (page - 1) * page_size
            return {
                "run": None,
                "run_files_count": 0,
                "summary": {
                    "total_files_on_disk": total,
                    "synced": 0,
                    "pending": total,
                    "modified": 0,
                    "failed": 0,
                },
                "pagination": {"page": page, "page_size": page_size, "total": total},
                "files": entries[start : start + page_size],
            }

        with self._session() as session:
            run_files_count = int(
                session.scalar(
                    select(func.count(IngestionFileStatus.id)).where(
                        IngestionFileStatus.run_id == run["id"]
                    )
                )
                or 0
            )
            files = session.execute(select(IndexedFile)).scalars().all()
            file_statuses = (
                session.execute(
                    select(IngestionFileStatus).where(
                        IngestionFileStatus.run_id == run["id"]
                    )
                )
                .scalars()
                .all()
            )
            chunk_traces = (
                session.execute(
                    select(ChunkTrace).where(ChunkTrace.run_id == run["id"])
                )
                .scalars()
                .all()
            )

        statuses_by_file_id = {status.file_id: status for status in file_statuses}
        chunk_counts_by_file_id: dict[int, int] = {}
        for chunk_trace in chunk_traces:
            chunk_counts_by_file_id[chunk_trace.file_id] = (
                chunk_counts_by_file_id.get(chunk_trace.file_id, 0) + 1
            )

        db_files: dict[str, dict] = {}
        for file in files:
            status_entry = statuses_by_file_id.get(file.id)
            db_files[file.file_path] = {
                "file_path": file.file_path,
                "file_run_status": status_entry.status if status_entry else None,
                "file_hash_at_run": status_entry.file_hash_at_run
                if status_entry
                else None,
                "error_message": status_entry.error_message if status_entry else None,
                "last_ingested_at": run.get("completed_at"),
                "chunks_count": chunk_counts_by_file_id.get(file.id, 0),
            }

        result: list[dict] = []
        counts: dict[str, int] = {"synced": 0, "modified": 0, "pending": 0, "failed": 0}

        for source in sorted(current_files):
            disk_checksum = current_files[source]
            db_row = db_files.get(source)
            if db_row is None:
                result.append(
                    {
                        "file_path": source,
                        "status": "pending",
                        "detail": "Detectado en disco. Aun no se han generado embeddings.",
                        "last_ingested_at": None,
                        "chunks_count": 0,
                    }
                )
                counts["pending"] += 1
                continue

            file_run_status = str(db_row["file_run_status"] or "").strip()
            file_hash_at_run = str(db_row["file_hash_at_run"] or "").strip()
            last_ingested_at = db_row["last_ingested_at"]
            chunks_count = int(db_row["chunks_count"] or 0)

            if file_run_status == "failed":
                entry: dict = {
                    "file_path": source,
                    "status": "failed",
                    "last_ingested_at": last_ingested_at,
                    "chunks_count": chunks_count,
                }
                if db_row.get("error_message"):
                    entry["detail"] = db_row["error_message"]
                result.append(entry)
                counts["failed"] += 1
            elif not file_hash_at_run:
                result.append(
                    {
                        "file_path": source,
                        "status": "pending",
                        "detail": "Detectado en disco. Aun no se han generado embeddings.",
                        "last_ingested_at": None,
                        "chunks_count": 0,
                    }
                )
                counts["pending"] += 1
            elif file_hash_at_run == disk_checksum:
                result.append(
                    {
                        "file_path": source,
                        "status": "synced",
                        "last_ingested_at": last_ingested_at,
                        "chunks_count": chunks_count,
                    }
                )
                counts["synced"] += 1
            else:
                result.append(
                    {
                        "file_path": source,
                        "status": "modified",
                        "detail": "El archivo físico cambió. Requiere re-indexación.",
                        "last_ingested_at": last_ingested_at,
                        "chunks_count": chunks_count,
                    }
                )
                counts["modified"] += 1

        total = len(result)
        start = (page - 1) * page_size
        return {
            "run": run,
            "run_files_count": run_files_count,
            "summary": {
                "total_files_on_disk": total,
                "synced": counts["synced"],
                "pending": counts["pending"],
                "modified": counts["modified"],
                "failed": counts["failed"],
            },
            "pagination": {"page": page, "page_size": page_size, "total": total},
            "files": result[start : start + page_size],
        }

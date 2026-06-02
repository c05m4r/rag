from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
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
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class QueryInteraction(Base):
    __tablename__ = "query_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queried_at: Mapped[str] = mapped_column(String, nullable=False)
    answered_at: Mapped[str | None] = mapped_column(String, nullable=True)
    interaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)


class QueryTraceabilityStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path.resolve()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        inspector = inspect(self.engine)
        if inspector.has_table("query_interactions"):
            columns = {
                column["name"] for column in inspector.get_columns("query_interactions")
            }
            with self.engine.begin() as conn:
                if "created_at" in columns and "queried_at" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE query_interactions "
                            "RENAME COLUMN created_at TO queried_at"
                        )
                    )
                if "answered_at" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE query_interactions "
                            "ADD COLUMN answered_at TEXT"
                        )
                    )
                    conn.execute(
                        text(
                            "UPDATE query_interactions "
                            "SET answered_at = queried_at "
                            "WHERE answered_at IS NULL"
                        )
                    )

        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_query_interactions_queried_at "
                    "ON query_interactions (queried_at DESC)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_query_interactions_type "
                    "ON query_interactions (interaction_type)"
                )
            )

    def _validate_interaction_type(self, interaction_type: str) -> str:
        normalized = interaction_type.strip().lower() or "query"
        if not re.fullmatch(r"[a-z0-9_-]{1,50}", normalized):
            raise ValueError("interaction_type must match [a-z0-9_-]{1,50}")
        return normalized

    def _normalize_sources(self, sources: list[dict[str, str]] | None) -> str:
        return json.dumps(sources or [], ensure_ascii=False)

    def record_interaction(
        self,
        *,
        interaction_type: str,
        question: str,
        answer: str,
        queried_at: str | None = None,
        sources: list[dict[str, str]] | None = None,
    ) -> int:
        interaction = QueryInteraction(
            queried_at=queried_at or datetime.now(UTC).isoformat(),
            answered_at=datetime.now(UTC).isoformat(),
            interaction_type=self._validate_interaction_type(interaction_type),
            question=question.strip(),
            answer=answer.strip(),
            sources_json=self._normalize_sources(sources),
        )
        with self.SessionLocal() as session:
            session.add(interaction)
            session.commit()
            session.refresh(interaction)
            return int(interaction.id)

    def list_interactions(
        self,
        page: int = 1,
        page_size: int = 10,
        interaction_type: str | None = None,
    ) -> dict:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        with self.SessionLocal() as session:
            query = select(QueryInteraction)
            count_query = select(func.count(QueryInteraction.id))
            if interaction_type:
                normalized_type = self._validate_interaction_type(interaction_type)
                query = query.where(
                    QueryInteraction.interaction_type == normalized_type
                )
                count_query = count_query.where(
                    QueryInteraction.interaction_type == normalized_type
                )

            total = int(session.execute(count_query).scalar_one())
            rows = (
                session.execute(
                    query.order_by(desc(QueryInteraction.queried_at))
                    .offset(offset)
                    .limit(page_size)
                )
                .scalars()
                .all()
            )

            interactions = []
            for row in rows:
                try:
                    sources = json.loads(row.sources_json or "[]")
                except json.JSONDecodeError:
                    sources = []
                interactions.append(
                    {
                        "id": row.id,
                        "queried_at": row.queried_at,
                        "answered_at": row.answered_at,
                        "interaction_type": row.interaction_type,
                        "question": row.question,
                        "answer": row.answer,
                        "sources": sources,
                    }
                )

            return {
                "page": page,
                "page_size": page_size,
                "total": total,
                "interactions": interactions,
            }

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rag.hashing import HashAlgorithm, normalize_hash_algorithm


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="env/.env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = Field(default="rag")
    log_level: str = Field(default="INFO")

    rag_root_path: Path = Field(default=Path("rag"))
    traceability_db_path: Path = Field(default=Path("data/traceability.db"))
    vector_state_dir: Path = Field(default=Path("data/vector_state"))
    turbovec_store_dir: Path = Field(default=Path("data/turbovec_index"))
    vector_store_provider: Literal["qdrant", "turbovec"] = "turbovec"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_name: str = "rag_docs"
    qdrant_api_key: str | None = None
    qdrant_prefer_grpc: bool = False

    chunk_size: int = Field(default=1200, ge=100)
    chunk_overlap: int = Field(default=150, ge=0)

    retrieval_k: int = Field(default=5, ge=1)
    turbovec_bit_width: int = Field(default=4, ge=2, le=4)
    hash_algorithm: HashAlgorithm = "xxhash"

    force_reindex: bool = False

    embeddings_provider: Literal["openai", "huggingface", "gemini", "ollama"] = "gemini"
    embeddings_model_openai: str = "text-embedding-3-small"
    embeddings_model_hf: str = "sentence-transformers/all-MiniLM-L6-v2"
    embeddings_model_gemini: str = "gemini-embedding-2-preview"
    embeddings_model_ollama: str = "nomic-embed-text"

    llm_provider: Literal["openai", "ollama", "huggingface", "gemini"] = "gemini"
    llm_model_openai: str = "gpt-4o-mini"
    llm_model_ollama: str = "llama3.1"
    llm_ollama_base_url: str = "http://localhost:11434"
    llm_model_hf: str = "mistralai/Mistral-7B-Instruct-v0.2"
    llm_model_gemini: str = "gemini-1.5-flash"
    huggingface_api_token: str | None = None
    llm_temperature: float = Field(default=0.1, ge=0.0, le=1.0)

    openai_api_key: str | None = None
    google_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_webhook_url: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be lower than CHUNK_SIZE")
        if self.turbovec_bit_width not in (2, 4):
            raise ValueError("TURBOVEC_BIT_WIDTH must be 2 or 4")
        self.hash_algorithm = normalize_hash_algorithm(self.hash_algorithm)
        if self.api_key and not self.telegram_webhook_secret:
            self.telegram_webhook_secret = self.api_key
        return self


settings = Settings()

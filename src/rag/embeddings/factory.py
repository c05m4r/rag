from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from rag.config import settings


def build_embeddings() -> Embeddings:
    if settings.embeddings_provider == "openai":
        return OpenAIEmbeddings(
            model=settings.embeddings_model_openai, api_key=settings.openai_api_key
        )
    if settings.embeddings_provider == "gemini":
        return GoogleGenerativeAIEmbeddings(
            model=settings.embeddings_model_gemini,
            google_api_key=settings.google_api_key,
        )
    if settings.embeddings_provider == "ollama":
        return OllamaEmbeddings(
            model=settings.embeddings_model_ollama,
            base_url=settings.llm_ollama_base_url,
        )
    return HuggingFaceEmbeddings(model_name=settings.embeddings_model_hf)

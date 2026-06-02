from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from rag.config import settings


def build_llm() -> BaseChatModel:
    if settings.llm_provider == "openai":
        return ChatOpenAI(
            model=settings.llm_model_openai,
            temperature=settings.llm_temperature,
            api_key=settings.openai_api_key,
        )
    if settings.llm_provider == "ollama":
        return ChatOllama(
            model=settings.llm_model_ollama,
            base_url=settings.llm_ollama_base_url,
            temperature=settings.llm_temperature,
        )
    if settings.llm_provider == "gemini":
        return ChatGoogleGenerativeAI(
            model=settings.llm_model_gemini,
            temperature=settings.llm_temperature,
            google_api_key=settings.google_api_key,
        )
    if settings.llm_provider == "huggingface":
        endpoint = HuggingFaceEndpoint(
            repo_id=settings.llm_model_hf,
            huggingfacehub_api_token=settings.huggingface_api_token,
            temperature=settings.llm_temperature,
        )
        return ChatHuggingFace(llm=endpoint)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_template(
        """
Eres un asistente de QA para documentos internos.
Responde de forma concisa y solo con base en el contexto recuperado.
Si no hay evidencia suficiente en el contexto, responde exactamente: No tengo evidencia suficiente en los documentos.

Contexto:
{context}

Pregunta:
{question}
""".strip()
    )


def format_context(docs: list) -> str:
    blocks: list[str] = []
    for i, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source", "unknown")
        blocks.append(f"[{i}] Fuente: {source}\n{doc.page_content}")
    return "\n\n---\n\n".join(blocks)

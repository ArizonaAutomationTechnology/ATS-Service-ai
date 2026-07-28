import json
import re
from functools import lru_cache
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

from app.config import settings


class LlmServiceError(RuntimeError):
    """Raised when Ollama cannot produce a usable response."""


@lru_cache(maxsize=4)
def get_chat_model(
    temperature: float = 0.8,
    timeout_seconds: float | None = None,
) -> ChatOllama:
    """Return the shared LangChain chat model for the configured Ollama server."""
    client_kwargs: dict[str, Any] = {
        "timeout": timeout_seconds or settings.OLLAMA_TIMEOUT_SECONDS,
    }
    if settings.OLLAMA_API_KEY:
        client_kwargs["headers"] = {
            "Authorization": f"Bearer {settings.OLLAMA_API_KEY}",
        }

    return ChatOllama(
        model=settings.OLLAMA_MODEL,
        base_url=settings.OLLAMA_URL,
        temperature=temperature,
        num_ctx=4096,
        client_kwargs=client_kwargs,
    )


@lru_cache(maxsize=1)
def get_text_chain():
    """Build the common LangChain runnable used by all free-text prompts."""
    prompt = ChatPromptTemplate.from_messages([("human", "{prompt}")])
    return prompt | get_chat_model() | StrOutputParser()


def parse_json_content(content: str) -> dict[str, Any]:
    """Parse JSON even when a model wraps it in a markdown code fence."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object.")
    return result


async def call_llm_async(prompt: str) -> str:
    """Generate a non-streaming response through LangChain's ChatOllama."""
    try:
        return (await get_text_chain().ainvoke({"prompt": prompt})).strip()
    except Exception as exc:
        return f"Error communicating with LLM service: {exc}"


async def call_llm_json_async(prompt: str, response_schema: dict) -> dict:
    """Generate a schema-constrained response through LangChain's ChatOllama."""
    chat_prompt = ChatPromptTemplate.from_messages([("human", "{prompt}")])
    structured_model = get_chat_model(
        0.2,
        settings.OLLAMA_DESCRIPTION_TIMEOUT_SECONDS,
    ).with_structured_output(
        response_schema,
        method="json_schema",
    )
    try:
        result = await (chat_prompt | structured_model).ainvoke({"prompt": prompt})
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        if isinstance(result, str):
            result = parse_json_content(result)
    except Exception as exc:
        raise LlmServiceError("Ollama could not generate a structured response.") from exc
    if not isinstance(result, dict):
        raise LlmServiceError("Ollama returned an invalid structured response.")
    return result

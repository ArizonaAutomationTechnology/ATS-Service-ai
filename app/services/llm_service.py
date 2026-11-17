import json
import logging
import re
from functools import lru_cache
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import settings


logger = logging.getLogger(__name__)


class LlmServiceError(RuntimeError):
    """Raised when the model cannot produce a usable response."""


@lru_cache(maxsize=4)
def get_chat_model(
    temperature: float = 0.8,
    timeout_seconds: float | None = None,
) -> ChatOpenAI:
    """Return the shared chat model on the Gemini Developer API."""
    api_key = settings.OPENAI_API_KEY.strip()
    if not api_key:
        raise LlmServiceError("Set OPENAI_API_KEY to enable model requests.")

    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=api_key,
        base_url=settings.OPENAI_BASE_URL,
        # vertexai=False,
        # api_version="v1beta",
        temperature=temperature,
        timeout=timeout_seconds or settings.OPENAI_TIMEOUT_SECONDS,
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
    """Generate a non-streaming text response through the shared model."""
    try:
        return (await get_text_chain().ainvoke({"prompt": prompt})).strip()
    except LlmServiceError as exc:
        return f"Error communicating with LLM service: {exc}"
    except Exception as exc:
        logger.warning("Gemini request failed (%s).", type(exc).__name__)
        return "Error communicating with LLM service: Gemini request failed."


async def call_llm_json_async(prompt: str, response_schema: type[BaseModel]) -> dict[str, Any]:
    """Request JSON from Gemma and validate the response against the supplied model."""
    try:
        model = get_chat_model(0.2, settings.GEMINI_DESCRIPTION_TIMEOUT_SECONDS)
        schema = json.dumps(response_schema.model_json_schema())
        result = await (model | StrOutputParser()).ainvoke(f"{prompt}\n\nJSON schema:\n{schema}")
        return response_schema.model_validate(parse_json_content(result), strict=True).model_dump()
    except LlmServiceError:
        raise
    except Exception as exc:
        raise LlmServiceError("Gemini could not generate a valid structured response.") from exc

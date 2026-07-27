import json


# pyrefly: ignore [missing-import]
import httpx
from app.config import settings


class LlmServiceError(RuntimeError):
    """Raised when Ollama cannot produce a usable response."""




async def call_llm_async(prompt: str) -> str:
    url = f"{settings.OLLAMA_URL}/api/generate"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.6, 
            "num_ctx": 4096,    
        }
    }
    # Using AsyncClient with a 300-second timeout
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
        except Exception as e:
            return f"Error communicating with LLM service: {str(e)}"


async def call_llm_json_async(prompt: str, response_schema: dict) -> dict:
    """Generate a non-streaming, schema-constrained JSON response with Ollama."""
    url = f"{settings.OLLAMA_URL}/api/chat"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "format": response_schema,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.OLLAMA_DESCRIPTION_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        content = response.json().get("message", {}).get("content", "").strip()
        result = json.loads(content)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise LlmServiceError("Ollama could not polish the ticket description.") from exc
    if not isinstance(result, dict):
        raise LlmServiceError("Ollama returned an invalid ticket description.")
    return result

# pyrefly: ignore [missing-import]
import httpx
from app.config import settings

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

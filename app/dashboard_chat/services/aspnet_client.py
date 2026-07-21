import httpx
from app.config import settings
from typing import Dict, Any

class AspNetClient:
    def __init__(self):
        self.base_url = settings.ASPNET_API_BASE_URL.rstrip('/')
        self.headers = {
            "XApiKey": settings.ASPNET_X_API_KEY,
            "Content-Type": "application/json"
        }

    def _build_url(self, endpoint: str) -> str:
        endpoint_clean = endpoint.lstrip('/')
        if self.base_url.endswith('/api') and endpoint_clean.startswith('api/'):
            endpoint_clean = endpoint_clean[4:]
        return f"{self.base_url}/{endpoint_clean}"

    async def get(self, endpoint: str) -> httpx.Response:
        url = self._build_url(endpoint)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response

    async def post(self, endpoint: str, json_data: Dict[str, Any]) -> httpx.Response:
        url = self._build_url(endpoint)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=self.headers, json=json_data)
            response.raise_for_status()
            return response

aspnet_client = AspNetClient()

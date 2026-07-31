import asyncio
from contextlib import asynccontextmanager
import httpx
from app.config import settings
from typing import Dict, Any, Iterable, Optional, Tuple

class AspNetClient:
    def __init__(self):
        self.base_url = settings.ASPNET_API_BASE_URL.rstrip('/')
        self.headers = {"XApiKey": settings.ASPNET_X_API_KEY}
        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def open(self) -> None:
        await self.aclose()
        self._client = httpx.AsyncClient(timeout=30.0)
        self._loop = asyncio.get_running_loop()

    @asynccontextmanager
    async def _client_for_request(self):
        if (
            self._client is not None
            and not self._client.is_closed
            and self._loop is asyncio.get_running_loop()
        ):
            yield self._client
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    async def aclose(self) -> None:
        client = self._client
        client_loop = self._loop
        self._client = None
        self._loop = None
        if (
            client is not None
            and not client.is_closed
            and client_loop is asyncio.get_running_loop()
        ):
            await client.aclose()

    def _build_url(self, endpoint: str) -> str:
        endpoint_clean = endpoint.lstrip('/')
        if self.base_url.endswith('/api') and endpoint_clean.startswith('api/'):
            endpoint_clean = endpoint_clean[4:]
        return f"{self.base_url}/{endpoint_clean}"

    async def get(self, endpoint: str) -> httpx.Response:
        url = self._build_url(endpoint)
        async with self._client_for_request() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response

    async def post(self, endpoint: str, json_data: Dict[str, Any]) -> httpx.Response:
        url = self._build_url(endpoint)
        async with self._client_for_request() as client:
            response = await client.post(
                url,
                headers={**self.headers, "Content-Type": "application/json"},
                json=json_data,
            )
            response.raise_for_status()
            return response

    async def post_form(
        self,
        endpoint: str,
        form_data: Iterable[Tuple[str, str]],
    ) -> httpx.Response:
        url = self._build_url(endpoint)
        files = [(name, (None, value)) for name, value in form_data]
        async with self._client_for_request() as client:
            response = await client.post(url, headers=self.headers, files=files)
            response.raise_for_status()
            return response

    async def put_form(
        self,
        endpoint: str,
        form_data: Iterable[Tuple[str, str]],
    ) -> httpx.Response:
        url = self._build_url(endpoint)
        files = [(name, (None, value)) for name, value in form_data]
        async with self._client_for_request() as client:
            response = await client.put(url, headers=self.headers, files=files)
            response.raise_for_status()
            return response

aspnet_client = AspNetClient()

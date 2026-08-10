import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from app.api.assistant import router as assistant_router
from app.dashboard_chat.router import router as dashboard_chat_router
from app.dashboard_chat.employee_router import router as employee_dashboard_chat_router
from app.dashboard_chat.services.aspnet_client import aspnet_client
from app.config import settings
import uvicorn


logger = logging.getLogger("uvicorn.error")


async def _keep_render_awake(url: str) -> None:
    url = f"{url.rstrip('/')}/"
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            await asyncio.sleep(10 * 60)
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Render keep-alive request failed: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await aspnet_client.open()
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    keep_alive_task = asyncio.create_task(_keep_render_awake(render_url)) if render_url else None
    try:
        yield
    finally:
        if keep_alive_task:
            keep_alive_task.cancel()
            with suppress(asyncio.CancelledError):
                await keep_alive_task
        await aspnet_client.aclose()


app = FastAPI(
    title="AI Ticket Assistant API",
    description="FastAPI service for ticket description summarization and assignment recommendations.",
    version="1.0.0",
    lifespan=lifespan,
    openapi_url=None if os.getenv("RENDER") else "/openapi.json",
)

# CORS middleware configurations to allow Angular frontend calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Set to specific domains like http://localhost:4200 in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include assistant routers
app.include_router(assistant_router)
app.include_router(dashboard_chat_router)
app.include_router(employee_dashboard_chat_router)


@app.get("/")
def read_root():
    return {"message": "AI Ticket Assistant API is running!"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=True)

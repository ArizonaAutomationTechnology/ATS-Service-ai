from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.assistant import router as assistant_router
from app.dashboard_chat.router import router as dashboard_chat_router
from app.dashboard_chat.employee_router import router as employee_dashboard_chat_router
from app.dashboard_chat.services.aspnet_client import aspnet_client
from app.config import settings
import uvicorn


@asynccontextmanager
async def lifespan(_: FastAPI):
    await aspnet_client.open()
    yield
    await aspnet_client.aclose()


app = FastAPI(
    title="AI Ticket Assistant API",
    description="FastAPI service for ticket description summarization and assignment recommendations.",
    version="1.0.0",
    lifespan=lifespan,
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

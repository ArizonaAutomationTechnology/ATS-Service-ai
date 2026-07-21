from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.assistant import router as assistant_router
from app.dashboard_chat.router import router as dashboard_chat_router
from app.config import settings
import uvicorn

app = FastAPI(
    title="AI Ticket Assistant API",
    description="FastAPI service for ticket description summarization and assignment recommendations.",
    version="1.0.0"
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


@app.get("/")
def read_root():
    return {"message": "AI Ticket Assistant API is running!"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=True)

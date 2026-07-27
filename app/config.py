import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings:
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+17+for+SQL+Server%7D%3BSERVER%3DDESKTOP-AIT044%3BDATABASE%3DATSDB%3BTrusted_Connection%3Dyes%3BTrustServerCertificate%3Dyes"
    )
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma4:cloud")
    PORT: int = int(os.getenv("PORT", "8000"))
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "medium")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    MAX_AUDIO_BYTES: int = int(os.getenv("MAX_AUDIO_BYTES", str(10 * 1024 * 1024)))
    MAX_AUDIO_DURATION_SECONDS: int = int(os.getenv("MAX_AUDIO_DURATION_SECONDS", "120"))
    MAX_TRANSCRIPT_CHARS: int = int(os.getenv("MAX_TRANSCRIPT_CHARS", "12000"))
    OLLAMA_DESCRIPTION_TIMEOUT_SECONDS: float = float(
        os.getenv("OLLAMA_DESCRIPTION_TIMEOUT_SECONDS", "90")
    )
    
    # Dashboard AI Chat settings
    ASPNET_API_BASE_URL: str = os.getenv("ASPNET_API_BASE_URL", "http://localhost:7122/api/")
    ASPNET_X_API_KEY: str = os.getenv("ASPNET_X_API_KEY", "pgH7QzFHJx4w46fI~5Uzi4RvtTwlEXp")
    DASHBOARD_CHAT_MAX_PAGE_SIZE: int = int(os.getenv("DASHBOARD_CHAT_MAX_PAGE_SIZE", "20"))
    DASHBOARD_CHAT_CONTEXT_LIMIT_KB: int = int(os.getenv("DASHBOARD_CHAT_CONTEXT_LIMIT_KB", "40"))

settings = Settings()

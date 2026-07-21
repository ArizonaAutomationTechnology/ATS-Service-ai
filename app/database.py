import logging
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

logger = logging.getLogger("uvicorn.error")

def create_resilient_engine():
    try:
        # Try primary connection URL first
        engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
        # Test connection
        with engine.connect() as conn:
            pass
        return engine
    except Exception as e:
        logger.warning(f"Database connection failed with primary URL: {str(e)}")
        
        # Check if URL was using a custom SQL login and try Windows Auth fallback
        if "ai_reader" in settings.DATABASE_URL or "UID=" in settings.DATABASE_URL or "uid=" in settings.DATABASE_URL:
            fallback_url = "mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+17+for+SQL+Server%7D%3BSERVER%3DDESKTOP-AIT044%3BDATABASE%3DATSDB%3BTrusted_Connection%3Dyes%3BTrustServerCertificate%3Dyes"
            logger.info("Attempting database connection fallback using Windows Authentication...")
            try:
                engine = create_engine(fallback_url, pool_pre_ping=True)
                with engine.connect() as conn:
                    pass
                logger.info("Database connection established successfully using fallback Windows Authentication.")
                return engine
            except Exception as fe:
                logger.error(f"Fallback connection also failed: {str(fe)}")
        
        # If fallback fails, return original engine to bubble up standard error
        return create_engine(settings.DATABASE_URL, pool_pre_ping=True)

engine = create_resilient_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to retrieve the DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

"""
Datenbank Service
"""
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.database import Base
from utils.config import settings

# Async Engine erstellen
engine = create_async_engine(
    settings.database_url.replace("postgresql://", "postgresql+asyncpg://"),
    echo=False,
    future=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=True,
)

# Session Factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    """Datenbank initialisieren und Tabellen erstellen"""
    try:
        async with engine.begin() as conn:
            # Ensure pgvector extension exists before creating tables with vector columns
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

            # Idempotent ALTER TABLE for new columns on existing conversation_memories
            # (create_all only creates new tables, not new columns on existing ones)
            alter_stmts = [
                "ALTER TABLE conversation_memories ADD COLUMN IF NOT EXISTS scope VARCHAR(10) NOT NULL DEFAULT 'user'",
                "ALTER TABLE conversation_memories ADD COLUMN IF NOT EXISTS team_id VARCHAR(100)",
                "ALTER TABLE conversation_memories ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'llm_inferred'",
                "ALTER TABLE conversation_memories ADD COLUMN IF NOT EXISTS confidence FLOAT NOT NULL DEFAULT 1.0",
                "ALTER TABLE conversation_memories ADD COLUMN IF NOT EXISTS trigger_pattern VARCHAR(255)",
            ]
            for stmt in alter_stmts:
                await conn.execute(text(stmt))

        logger.info("✅ Datenbank-Tabellen erstellt")
    except Exception as e:
        logger.error(f"❌ Fehler beim Initialisieren der Datenbank: {e}")
        raise

async def get_db():
    """Dependency für FastAPI Endpoints"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

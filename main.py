"""
main.py
נקודת הכניסה של FastAPI — init pool, routes, lifespan
"""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from db.client  import init_pool, close_pool
from db.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    logger.info("Starting ICR Engine...")
    await init_pool(
        dsn=settings.DATABASE_URL,
        min_size=settings.DB_POOL_MIN,
        max_size=settings.DB_POOL_MAX,
    )

    # import כאן כדי להימנע מ-circular imports
    from triggers.scheduler import start_scheduler
    await start_scheduler()

    logger.info("ICR Engine ready ✓")
    yield

    # ── Shutdown ─────────────────────────────────────────────
    logger.info("Shutting down ICR Engine...")
    from triggers.scheduler import stop_scheduler
    await stop_scheduler()
    await close_pool()


app = FastAPI(
    title="ICR Engine",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Routes ────────────────────────────────────────────────────
from api.webhook_register import router as register_router
from api.webhook_incoming import router as incoming_router
from api.service_trigger  import router as trigger_router

app.include_router(register_router, prefix="/webhook", tags=["webhook"])
app.include_router(incoming_router, prefix="/webhook", tags=["webhook"])
app.include_router(trigger_router,  prefix="/trigger",  tags=["trigger"])


@app.get("/health")
async def health():
    return {"status": "ok"}

"""
Data Spine — startup registers with all active agents automatically.
"""
import os, logging, asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from dependencies import get_supabase
from routers import calls, send, incoming, worker_events, webhooks, conversations, notifications

log = logging.getLogger("spine")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s — %(message)s", datefmt="%H:%M:%S")

SPINE_SELF_URL = os.getenv("SPINE_SELF_URL", "http://scenario_data-spine:8000")


async def register_with_agents():
    """
    On startup — register Spine as webhook listener with every active agent.
    Reads phone_workers (running) → finds agent URL → registers callback.
    """
    await asyncio.sleep(3)  # wait for network
    db = get_supabase()

    workers = db.table("phone_workers") \
        .select("phone_id, service_name") \
        .eq("status", "running").execute().data or []

    # Also check spine_webhooks for known agent URLs
    hooks = db.table("spine_webhooks") \
        .select("phone_id, agent_url") \
        .eq("status", "active").execute().data or []

    agent_map = {h["phone_id"]: h["agent_url"] for h in hooks}

    registered = 0
    for w in workers:
        pid = w["phone_id"]
        agent_url = agent_map.get(pid)
        if not agent_url:
            continue

        callback = f"{SPINE_SELF_URL}/incoming/{pid}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{agent_url}/webhook/register", json={
                    "url": callback,
                    "events": ["message", "status_update"],
                })
            registered += 1
            log.info("Registered with agent | phone=%s agent=%s", pid, agent_url)
        except Exception as e:
            log.warning("Failed to register with agent | phone=%s: %s", pid, e)

    log.info("Startup registration complete | %d/%d agents", registered, len(workers))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — register with agents
    asyncio.create_task(register_with_agents())
    log.info("Spine started | self_url=%s", SPINE_SELF_URL)
    yield
    log.info("Spine shutting down")


app = FastAPI(title="Data Spine", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(calls.router)
app.include_router(send.router)
app.include_router(incoming.router)
app.include_router(worker_events.router)
app.include_router(webhooks.router)
app.include_router(conversations.router, prefix="/api")
app.include_router(notifications.router)

@app.get("/")
def root():
    return {"service": "data-spine", "status": "online"}

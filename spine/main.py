import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import calls, send, incoming, worker_events, webhooks, conversations, notifications

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="Data Spine", version="1.0.0", lifespan=lifespan)
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

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import webhook, conversations, dispatch

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="Data Spine", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(webhook.router)
app.include_router(conversations.router, prefix="/api")
app.include_router(dispatch.router, prefix="/api")

@app.get("/")
def root():
    return {"service": "data-spine", "status": "online"}

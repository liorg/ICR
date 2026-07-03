"""
routers/spine.py — vid.michal-solutions.com

Proxy router שמעביר קריאות מ-React ל-Spine service.
React קורא: apiFetch("/spine/...") → FastAPI → Spine (:8100)

הוסף ל-main.py:
  from routers import spine
  app.include_router(spine.router, prefix="/api")
"""

import os
import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/spine", tags=["spine-proxy"])

SPINE_URL = os.getenv("SPINE_URL", "http://127.0.0.1:8100")


@router.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def proxy_to_spine(path: str, request: Request):
    """Forward all /api/spine/* requests to standalone Spine service."""
    url = f"{SPINE_URL}/{path}"

    # Forward query params
    if request.query_params:
        url += f"?{request.query_params}"

    # Forward body for non-GET
    body = None
    if request.method != "GET":
        body = await request.body()

    # Forward headers (keep auth)
    headers = {}
    if "authorization" in request.headers:
        headers["authorization"] = request.headers["authorization"]
    if "content-type" in request.headers:
        headers["content-type"] = request.headers["content-type"]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            content=body,
            headers=headers,
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )

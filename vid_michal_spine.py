"""routers/spine.py for vid.michal — proxy to Spine :8001"""
import os, httpx
from fastapi import APIRouter, Request, Response
router = APIRouter(prefix="/spine", tags=["spine-proxy"])
SPINE_URL = os.getenv("SPINE_URL", "http://127.0.0.1:8001")

@router.api_route("/{path:path}", methods=["GET","POST","PATCH","PUT","DELETE"])
async def proxy(path: str, request: Request):
    url = f"{SPINE_URL}/{path}"
    if request.query_params: url += f"?{request.query_params}"
    body = await request.body() if request.method != "GET" else None
    headers = {k: request.headers[k] for k in ("authorization","content-type") if k in request.headers}
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.request(request.method, url, content=body, headers=headers)
    return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type","application/json"))

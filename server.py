import sys
import os
import json
import time
from pathlib import Path
from contextlib import asynccontextmanager
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_ME = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
sys.path.insert(0, str(_ME))

from core.agent import Agent, MODE_META

agent: Agent | None = None
BASE = _ME
CONFIG_FILE = Path(_ME) / "config.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    agent = Agent()
    yield
    if agent:
        await agent.close()


app = FastAPI(lifespan=lifespan, title="Mochi Agent")

# Security config
API_TOKEN = os.environ.get("MOCHI_API_TOKEN", "")
TRUSTED_ORIGINS = os.environ.get("MOCHI_TRUSTED_ORIGINS", "").split(",") if os.environ.get("MOCHI_TRUSTED_ORIGINS") else ["http://localhost:8000", "http://127.0.0.1:8000"]

# Rate limiting
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 30  # requests per minute per IP


app.add_middleware(
    CORSMiddleware,
    allow_origins=TRUSTED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=True,
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Skip security checks for CORS preflight
    if request.method == "OPTIONS":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _rate_limit_store[client_ip]
    window[:] = [t for t in window if t > now - 60]
    if len(window) >= RATE_LIMIT:
        return JSONResponse(status_code=429, content={"error": "Trop de requêtes"})
    window.append(now)

    sensitive = {"/chat", "/chat/stream", "/settings", "/mode", "/reset", "/conversation/log", "/undo", "/redo"}
    if API_TOKEN and request.url.path in sensitive:
        auth = request.headers.get("Authorization", "")
        token_param = request.query_params.get("token", "")
        if auth != f"Bearer {API_TOKEN}" and token_param != API_TOKEN:
            return JSONResponse(status_code=401, content={"error": "Non autorisé"})

    response = await call_next(request)
    return response

FRONTEND = BASE / "frontend"
FRONTEND.mkdir(exist_ok=True)

if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="frontend")


class ChatIn(BaseModel):
    message: str
    mode: str | None = None


class ModeIn(BaseModel):
    mode: str


class SettingsIn(BaseModel):
    mode: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    workspace: str | None = None


@app.get("/modes")
async def get_modes():
    modes = []
    for key, meta in MODE_META.items():
        mc = agent.config["modes"].get(key, {})
        modes.append({
            "key": key,
            "label": meta["label"],
            "desc": meta["desc"],
            "model": mc.get("model", ""),
            "tools": mc.get("tools", False),
        })
    return {"modes": modes, "current": agent.current_mode}


@app.post("/mode")
async def set_mode(body: ModeIn):
    if body.mode not in MODE_META:
        return JSONResponse(status_code=400, content={"error": f"Mode '{body.mode}' inconnu"})
    agent.set_mode(body.mode)
    mc = agent.config["modes"].get(body.mode, {})
    return {
        "mode": body.mode,
        "label": MODE_META[body.mode]["label"],
        "model": mc.get("model", ""),
        "tools": mc.get("tools", False),
    }


@app.get("/settings")
async def get_settings():
    mc = agent._mc()
    from core.tools import get_workspace
    # Charger le workspace sauvegardé s'il existe
    saved_workspace = None
    try:
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            saved_workspace = cfg.get("workspace")
    except Exception:
        pass
    return {
        "mode": agent.current_mode,
        "model": mc.get("model", ""),
        "max_tokens": mc.get("max_tokens", 4096),
        "temperature": mc.get("temperature", 0.7),
        "workspace": saved_workspace or str(get_workspace()),
        "label": MODE_META.get(agent.current_mode, {}).get("label", agent.current_mode),
    }


@app.post("/settings")
async def set_settings(body: SettingsIn):
    target = body.mode or agent.current_mode
    if target not in agent.modes:
        return JSONResponse(status_code=400, content={"error": f"Mode '{target}' inconnu"})
    old_mode = agent.current_mode
    agent.current_mode = target
    mc = agent._mc()
    if body.model is not None:
        mc["model"] = body.model
    if body.max_tokens is not None:
        mc["max_tokens"] = body.max_tokens
    if body.temperature is not None:
        mc["temperature"] = body.temperature
    if body.workspace is not None:
        from core.tools import set_workspace
        set_workspace(body.workspace)
        # Persist workspace dans config.json
        try:
            if CONFIG_FILE.exists():
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            else:
                cfg = {}
            cfg["workspace"] = body.workspace
            CONFIG_FILE.write_text(
                json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass
    agent.current_mode = old_mode
    agent._invalidate_mc()
    from core.tools import get_workspace
    return {
        "mode": target,
        "model": mc.get("model", ""),
        "max_tokens": mc.get("max_tokens", 4096),
        "temperature": mc.get("temperature", 0.7),
        "workspace": str(get_workspace()),
    }


@app.get("/stats")
async def get_stats():
    try:
        return agent.show_stats()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/reset")
async def reset():
    agent.clear_memory()
    return {"status": "ok"}


@app.post("/undo")
async def undo():
    from core.tools import _undo_tool
    result = _undo_tool()
    return {"status": "ok", "message": result["summary"], "data": result["data"]}


@app.post("/redo")
async def redo():
    from core.tools import _redo_tool
    result = _redo_tool()
    return {"status": "ok", "message": result["summary"], "data": result["data"]}


@app.post("/chat")
async def chat(body: ChatIn):
    try:
        if body.mode and body.mode in MODE_META:
            agent.set_mode(body.mode)
        content = await agent.gen(body.message)
        mc = agent.config["modes"].get(agent.current_mode, {})
        return {
            "reply": content,
            "mode": agent.current_mode,
            "model": mc.get("model", ""),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"reply": f"[Erreur] {e}"})


@app.post("/chat/stream")
async def chat_stream(body: ChatIn):
    if body.mode and body.mode in MODE_META:
        agent.set_mode(body.mode)

    async def sse():
        try:
            async for event, data in agent.generate_stream(body.message):
                if event == "content":
                    yield f"data: {json.dumps({'type': 'content', 'data': data}, ensure_ascii=False)}\n\n"
                elif event == "tool_call":
                    name, args = data
                    yield f"data: {json.dumps({'type': 'tool_call', 'name': name, 'args': args}, ensure_ascii=False)}\n\n"
                elif event == "tool_result":
                    name, result = data
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': name, 'result': result}, ensure_ascii=False)}\n\n"
                elif event == "error":
                    yield f"data: {json.dumps({'type': 'error', 'data': data}, ensure_ascii=False)}\n\n"
                    break
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.get("/conversation/log")
async def conversation_log():
    msgs = agent.memory.messages
    lines = []
    for m in msgs:
        ts = m.get("ts", "")
        role = m["role"]
        content = m.get("content", "")
        lines.append(f"[{ts}] {role.upper()}\n{content}\n")
    text = "\n---\n".join(lines)
    return Response(content=text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=conversation_log.txt"})


@app.get("/{path:path}")
async def serve_frontend(path: str):
    # Prevent path traversal in frontend routes
    p = (FRONTEND / path).resolve()
    try:
        p.relative_to(FRONTEND.resolve())
    except ValueError:
        return JSONResponse(status_code=404, content={"error": "not found"})
    if p.exists() and p.is_file():
        return FileResponse(p)
    index = FRONTEND / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"error": "not found"})


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("MOCHI_HOST", "127.0.0.1")
    uvicorn.run("server:app", host=host, port=8000, reload=False)

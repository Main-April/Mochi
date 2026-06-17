import sys
import os
import json
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_ME = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
sys.path.insert(0, str(_ME))

from agent import Agent, MODE_META

agent: Agent | None = None
BASE = _ME


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    agent = Agent()
    yield
    if agent:
        await agent.close()


app = FastAPI(lifespan=lifespan, title="Mochi Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    from tools import WORKSPACE
    return {
        "mode": agent.current_mode,
        "model": mc.get("model", ""),
        "max_tokens": mc.get("max_tokens", 4096),
        "temperature": mc.get("temperature", 0.7),
        "workspace": str(WORKSPACE),
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
        from tools import set_workspace
        set_workspace(body.workspace)
    agent.current_mode = old_mode
    agent._invalidate_mc()
    from tools import WORKSPACE
    return {
        "mode": target,
        "model": mc.get("model", ""),
        "max_tokens": mc.get("max_tokens", 4096),
        "temperature": mc.get("temperature", 0.7),
        "workspace": str(WORKSPACE),
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
    p = FRONTEND / path
    if p.exists() and p.is_file():
        return FileResponse(p)
    index = FRONTEND / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"error": "not found"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)

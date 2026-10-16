"""Web server: serves the app's page and its API.

Start with Start.bat (Windows) or:  python -m app.main
Then open http://127.0.0.1:8765

The server listens on this computer only (127.0.0.1), not on the network.
Long jobs (AI replies, builds, flashing, simulation) are streamed to the page
as Server-Sent Events; the serial monitor uses a WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from typing import Any, Iterator

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import projects
from app.assistant import Assistant
from app.config import (APP_NAME, APP_VERSION, GROQ_MODELS, WEB_DIR, ensure_dirs, load_settings,
                        public_settings, save_settings)
from app.esp import idf as idf_mod
from app.esp import tools
from app.esp.monitor import SerialMonitor, close_active, set_active
from app.llm.providers import OllamaProvider, make_provider

HOST = "127.0.0.1"
PORT = int(os.environ.get("ADEP_PORT", "8765"))

app = FastAPI(title=APP_NAME, version=APP_VERSION)
ensure_dirs()

# Tests replace this to use a scripted model instead of a real one.
assistant_factory = lambda settings: Assistant(settings)  # noqa: E731


def sse(events: Iterator[dict[str, Any]]) -> StreamingResponse:
    def gen():
        try:
            for event in events:
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception as exc:  # never leave the page waiting
            yield "data: " + json.dumps({"type": "error", "text": f"Unexpected error: {exc}"}) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _project(project_id: str) -> projects.Project:
    try:
        return projects.get(project_id)
    except projects.ProjectNotFound:
        raise HTTPException(404, "Project not found")


# ------------------------------------------------------------------- pages
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ------------------------------------------------------------------ status
@app.get("/api/status")
def status() -> dict[str, Any]:
    settings = load_settings()
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "esp_idf": idf_mod.status(settings),
        "provider": settings["provider"],
        "model": settings["groq_model"] if settings["provider"] == "groq" else settings["ollama_model"],
        "groq_key_set": bool(settings["groq_api_key"]),
        "monaco_local": (WEB_DIR / "vendor" / "monaco" / "vs" / "loader.js").exists(),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"settings": public_settings(), "groq_models": GROQ_MODELS}


@app.put("/api/settings")
def put_settings(changes: dict[str, Any] = Body(...)) -> dict[str, Any]:
    saved = save_settings(changes)
    return {"settings": public_settings(saved)}


@app.post("/api/settings/test")
def test_model() -> dict[str, Any]:
    ok, message = make_provider(load_settings()).check()
    return {"ok": ok, "message": message}


@app.get("/api/ollama/models")
def ollama_models() -> dict[str, Any]:
    settings = load_settings()
    try:
        return {"ok": True, "models": OllamaProvider(host=settings["ollama_host"]).installed_models()}
    except Exception as exc:
        return {"ok": False, "models": [], "message": str(exc)}


# ---------------------------------------------------------------- projects
@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    return projects.list_all()


@app.post("/api/projects")
def create_project(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return projects.create(str(body.get("name", ""))).full()


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return _project(project_id).full()


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    p = _project(project_id)
    p.update_meta(**{k: v for k, v in body.items() if k in ("name", "board")})
    return p.full()


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, Any]:
    _project(project_id)
    projects.delete(project_id)
    return {"ok": True}


@app.get("/api/projects/{project_id}/versions/{n}")
def get_version(project_id: str, n: int) -> dict[str, Any]:
    try:
        return {"n": n, "code": _project(project_id).code(n)}
    except KeyError:
        raise HTTPException(404, "Version not found")


@app.post("/api/projects/{project_id}/code")
def save_code(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    p = _project(project_id)
    entry = p.add_version(str(body.get("code", "")), str(body.get("source", "user")),
                          str(body.get("note", "Your manual edits")))
    return {"version": entry, "project": p.full()}


@app.post("/api/projects/{project_id}/restore/{n}")
def restore(project_id: str, n: int) -> dict[str, Any]:
    p = _project(project_id)
    try:
        code = p.code(n)
    except KeyError:
        raise HTTPException(404, "Version not found")
    entry = p.add_version(code, "restore", f"Restored version {n}")
    return {"version": entry, "project": p.full()}


@app.post("/api/projects/{project_id}/clear-chat")
def clear_chat(project_id: str) -> dict[str, Any]:
    p = _project(project_id)
    p.clear_chat()
    return p.full()


@app.get("/api/projects/{project_id}/export")
def export(project_id: str) -> Response:
    p = _project(project_id)
    name = (p.meta().get("name") or "project").replace('"', "")
    return Response(p.export_zip(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


@app.post("/api/projects/{project_id}/open-folder")
def open_folder(project_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    folder = _project(project_id).firmware_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if body.get("app") == "vscode":
        code = shutil.which("code") or shutil.which("code.cmd")
        if not code:
            return {"ok": False, "message": "VS Code's 'code' command was not found."}
        subprocess.Popen([code, str(folder)])
        return {"ok": True, "message": f"Opening {folder} in VS Code"}
    if sys.platform.startswith("win"):
        os.startfile(folder)  # type: ignore[attr-defined]
        return {"ok": True, "message": f"Opened {folder}"}
    return {"ok": True, "message": str(folder)}


# --------------------------------------------------------- streamed actions
@app.post("/api/projects/{project_id}/chat")
def chat(project_id: str, body: dict[str, Any] = Body(...)) -> StreamingResponse:
    p = _project(project_id)
    a = assistant_factory(load_settings())
    return sse(a.chat_turn(p, str(body.get("message", "")), body.get("code")))


@app.post("/api/projects/{project_id}/explain")
def explain(project_id: str, body: dict[str, Any] = Body(default={})) -> StreamingResponse:
    p = _project(project_id)
    return sse(assistant_factory(load_settings()).explain(p, str(body.get("code") or "")))


def _save_editor(p: projects.Project, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    code = body.get("code")
    if code and code.strip() != p.code().strip():
        entry = p.add_version(code, "user", "Your manual edits")
        if entry:
            yield {"type": "version", "version": entry, "code": code}


@app.post("/api/projects/{project_id}/build")
def build(project_id: str, body: dict[str, Any] = Body(default={})) -> StreamingResponse:
    p = _project(project_id)
    a = assistant_factory(load_settings())

    def events():
        yield from _save_editor(p, body)
        yield from a.build_and_fix(p, auto_fix=bool(body.get("fix", False)))
    return sse(events())


@app.post("/api/projects/{project_id}/simulate")
def simulate(project_id: str, body: dict[str, Any] = Body(default={})) -> StreamingResponse:
    p = _project(project_id)
    a = assistant_factory(load_settings())

    def events():
        yield from _save_editor(p, body)
        yield from a.simulate(p)
    return sse(events())


@app.post("/api/projects/{project_id}/flash")
def flash(project_id: str, body: dict[str, Any] = Body(...)) -> StreamingResponse:
    p = _project(project_id)
    a = assistant_factory(load_settings())

    def events():
        yield from _save_editor(p, body)
        yield from a.flash(p, str(body.get("port", "")))
    return sse(events())


@app.get("/api/ports")
def ports() -> list[dict[str, Any]]:
    return tools.list_ports()


# --------------------------------------------------------- serial monitor
@app.websocket("/ws/monitor")
async def monitor(ws: WebSocket) -> None:
    await ws.accept()
    port = ws.query_params.get("port", "")
    baud = int(ws.query_params.get("baud") or load_settings()["serial_baud"])
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_line(line: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "line", "text": line})

    def on_close(reason: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "closed", "text": reason})

    mon = SerialMonitor(port, baud, on_line, on_close)
    try:
        await asyncio.to_thread(mon.open)
    except RuntimeError as exc:
        await ws.send_json({"type": "error", "text": str(exc)})
        await ws.close()
        return
    set_active(mon)
    await ws.send_json({"type": "open", "text": f"Connected to {port} at {baud} baud."})

    async def pump() -> None:
        while True:
            item = await queue.get()
            await ws.send_json(item)
            if item["type"] == "closed":
                return

    sender = asyncio.create_task(pump())
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "send":
                mon.write(str(msg.get("text", "")) + ("\r\n" if msg.get("newline", True) else ""))
            elif msg.get("type") == "reset":
                mon.reset_board()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        mon.close()
        sender.cancel()


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


def run(open_browser: bool = True) -> None:
    import uvicorn

    url = f"http://{HOST}:{PORT}"
    print(f"\n  {APP_NAME} {APP_VERSION}\n  Open {url} in your browser. Close this window to stop.\n")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    run(open_browser="--no-browser" not in sys.argv)

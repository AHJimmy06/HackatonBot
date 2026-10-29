"""FastAPI es el marco: rutas, tiempo real y el panel.

El conector de Telegram, el pipeline del modelo y la base de datos
viven en paquetes distintos. Este archivo solo los enchufa.
"""

import asyncio
import json
import logging
import base64
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import LLM_API_KEY, LLM_MODEL, SCRAPE_EVERY_HOURS, STATIC_DIR, TELEGRAM_BOT_TOKEN
from app.config import DASHBOARD_PASSWORD
from app.persistence.database import get_meta
from app.catalog.scraper import refresh
from app.connectors.telegram import poll_forever
from app.orchestration.conversations import handle_incoming, operator_message, set_mode
from app.persistence.database import (
    get_conversation,
    init_db,
    list_conversations,
    list_courses,
    list_messages,
    metrics,
)
from app.realtime.hub import broadcast, subscribe, unsubscribe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _http_logger in ("httpx", "httpx2", "httpcore", "httpcore2"):
    logging.getLogger(_http_logger).setLevel(logging.ERROR)
log = logging.getLogger("ctt")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()

    async def boot_scrape() -> None:
        try:
            count = await refresh()
            log.info("Pénsum actualizado: %s cursos", count)
            await broadcast({"type": "catalog"})
        except Exception:
            log.exception("No se pudo leer el pénsum al arrancar")

    async def scrape_loop() -> None:
        while True:
            await asyncio.sleep(max(SCRAPE_EVERY_HOURS, 0.5) * 3600)
            try:
                await refresh()
                await broadcast({"type": "catalog"})
            except Exception:
                log.exception("Falló la actualización programada del pénsum")

    scrape_task = asyncio.create_task(boot_scrape())
    loop_task = asyncio.create_task(scrape_loop())
    bot_task = asyncio.create_task(poll_forever())
    yield
    for task in (scrape_task, loop_task, bot_task):
        if task is None:
            continue
        task.cancel()


app = FastAPI(title="Asesor CTT-FISEI", lifespan=lifespan)
@app.middleware("http")
async def protect_dashboard(request: Request, call_next):
    if DASHBOARD_PASSWORD:
        try:
            scheme, value = request.headers.get("authorization", "").split(" ", 1)
            username, password = base64.b64decode(value).decode().split(":", 1)
            valid = scheme.lower() == "basic" and hmac.compare_digest(username, "admin") and hmac.compare_digest(password, DASHBOARD_PASSWORD)
        except Exception:
            valid = False
        if not valid:
            return JSONResponse({"detail":"Autenticación requerida"},status_code=401,headers={"WWW-Authenticate":'Basic realm="CTT Connect"'})
    elif request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse({"detail":"Configura el archivo .dashboard-password"},status_code=403)
    if request.method not in {"GET","HEAD","OPTIONS"}:
        from urllib.parse import urlparse
        origin=request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail":"Origen no permitido"},status_code=403)
    return await call_next(request)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SimulateBody(BaseModel):
    telegram_chat_id: int = Field(lt=0)
    name: str = "Prospecto de prueba"
    username: str = ""
    text: str = Field(min_length=1, max_length=4000)


class OperatorBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ModeBody(BaseModel):
    mode: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "telegram": bool(TELEGRAM_BOT_TOKEN),
        "llm": bool(LLM_API_KEY),
        "model": LLM_MODEL if LLM_API_KEY else "",
        "telegram_status": get_meta("telegram_status", "configured" if TELEGRAM_BOT_TOKEN else "unconfigured"),
        "telegram_error": get_meta("telegram_error"),
        "protected": bool(DASHBOARD_PASSWORD),
    }


@app.get("/api/metrics")
def get_metrics():
    data = metrics()
    data["telegram"] = bool(TELEGRAM_BOT_TOKEN)
    data["llm"] = bool(LLM_API_KEY)
    return data


@app.get("/api/conversations")
def conversations():
    return list_conversations()


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: int):
    found = get_conversation(conversation_id)
    if not found:
        raise HTTPException(404, "Esa conversación no existe.")
    found["messages"] = list_messages(conversation_id)
    return found


@app.post("/api/conversations/{conversation_id}/messages")
async def post_message(conversation_id: int, body: OperatorBody):
    try:
        return await operator_message(conversation_id, body.text)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/conversations/{conversation_id}/mode")
async def post_mode(conversation_id: int, body: ModeBody):
    try:
        return await set_mode(conversation_id, body.mode)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/simulate")
async def simulate(body: SimulateBody):
    conversation = await handle_incoming(
        telegram_chat_id=body.telegram_chat_id,
        name=body.name.strip() or "Prospecto de prueba",
        username=body.username.strip(),
        text=body.text.strip(),
        origin="panel",
        deliver=False,
    )
    return conversation


@app.get("/api/catalog")
def catalog():
    return list_courses()


@app.post("/api/catalog/refresh")
async def catalog_refresh():
    try:
        count = await refresh()
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer el sitio del CTT: {exc}") from exc
    await broadcast({"type": "catalog"})
    return {"count": count}


@app.get("/api/events")
async def events():
    queue = subscribe()

    async def stream():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

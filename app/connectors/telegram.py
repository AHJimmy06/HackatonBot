import logging

import httpx

from app.config import TELEGRAM_BOT_TOKEN
from app.persistence.database import get_meta, set_meta

log = logging.getLogger(__name__)
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _keyboard(buttons):
    """Filas de {"text","data"} o {"text","url"} al formato inline_keyboard de Telegram."""
    if not buttons:
        return None
    return {"inline_keyboard": [
        [{"text": b["text"][:64], **({"url": b["url"]} if "url" in b else {"callback_data": b["data"][:64]})}
         for b in row] for row in buttons]}


async def send_message(chat_id: int, text: str, buttons: list | None = None, html: bool = False) -> bool:
    if not TELEGRAM_BOT_TOKEN or not text:
        return False
    chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [text]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for index, chunk in enumerate(chunks):
                body = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
                markup = _keyboard(buttons) if index == len(chunks) - 1 else None
                if markup:
                    body["reply_markup"] = markup
                if html:
                    body["parse_mode"] = "HTML"
                response = await client.post(f"{API}/sendMessage", json=body)
                data = response.json()
                if not data.get("ok") and html:
                    # HTML roto (p.ej. redacción del LLM): se reintenta en texto plano
                    import re
                    body.pop("parse_mode", None)
                    body["text"] = re.sub(r"<[^>]+>", "", chunk)
                    data = (await client.post(f"{API}/sendMessage", json=body)).json()
                if not data.get("ok"):
                    log.warning("Telegram rechazó el envío: %s", data.get("description"))
                    return False
        return True
    except Exception:
        log.exception("No se pudo enviar a Telegram")
        return False


async def answer_callback(callback_id: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{API}/answerCallbackQuery", json={"callback_query_id": callback_id})
    except Exception:
        log.debug("No se pudo responder el callback", exc_info=True)


async def typing(chat_id: int) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{API}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
    except Exception:
        log.debug("No se pudo mostrar escribiendo", exc_info=True)


async def poll_forever() -> None:
    from app.orchestration.conversations import handle_incoming

    if not TELEGRAM_BOT_TOKEN:
        log.warning("Sin TELEGRAM_BOT_TOKEN. El panel queda activo y el bot no escucha.")
        return
    offset = int(get_meta("telegram_offset", "0"))
    async with httpx.AsyncClient(timeout=40) as client:
        # Do not alter an existing deployment's webhook automatically.
        while True:
            try:
                response = await client.get(
                    f"{API}/getUpdates",
                    params={"timeout": 10, "offset": offset, "allowed_updates": '["message","callback_query"]'},
                )
                data = response.json()
                if not data.get("ok"):
                    set_meta("telegram_status", "error")
                    set_meta("telegram_error", str(data.get("description", "Error Telegram")))
                    log.warning("getUpdates: %s", data)
                    await _sleep()
                    continue
                for update in data.get("result") or []:
                    await _on_update(update)
                    offset = update["update_id"] + 1
                    set_meta("telegram_offset", str(offset))
                set_meta("telegram_status", "connected")
                set_meta("telegram_error", "")
            except Exception as exc:
                set_meta("telegram_status", "error")
                set_meta("telegram_error", type(exc).__name__)
                log.warning("Polling de Telegram: %s", type(exc).__name__)
                await _sleep()


async def _sleep() -> None:
    import asyncio

    await asyncio.sleep(2)


async def _on_update(update: dict) -> None:
    from app.orchestration.conversations import handle_callback, handle_incoming

    callback = update.get("callback_query")
    if callback:
        await answer_callback(callback.get("id", ""))
        chat = (callback.get("message") or {}).get("chat") or {}
        if chat.get("id") is None or chat.get("type") != "private":
            return
        user = callback.get("from") or {}
        name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part).strip()
        await handle_callback(telegram_chat_id=int(chat["id"]), name=name or "Prospecto",
                              username=user.get("username") or "", data=callback.get("data") or "")
        return

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat.get("type") != "private":
        return
    user = message.get("from") or {}
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part).strip()
    username = user.get("username") or ""
    if message.get("voice") or message.get("audio"):
        text = "[Envió una nota de voz]"
    elif message.get("document") or message.get("photo"):
        text = "[Envió un archivo]"
    elif message.get("sticker") or message.get("video") or message.get("video_note") or message.get("location") or message.get("contact"):
        text = "[Envió un sticker u otro contenido]"
    else:
        text = (message.get("text") or message.get("caption") or "").strip()
    if not text:
        return
    await handle_incoming(
        telegram_chat_id=int(chat_id),
        name=name or "Prospecto",
        username=username,
        text=text,
        origin="telegram",
        deliver=True,
    )

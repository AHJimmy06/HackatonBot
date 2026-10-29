import asyncio
from collections import defaultdict

_epochs = defaultdict(int)
_chat_locks = defaultdict(asyncio.Lock)
_send_locks = defaultdict(asyncio.Lock)

import json
import logging

from app.connectors.telegram import send_message, typing
from app.inference import guard
from app.inference.coherence import clarify, looks_incoherent, official_signup
from app.inference.pipeline import answer
from app.persistence import database as db
from app.realtime.hub import broadcast

log = logging.getLogger(__name__)

FILE_NOTE = "[Envió un archivo]"
VOICE_REPLY = (
    "Si me lo escribes lo leo enseguida. Cuéntame qué quieres aprender y si eres estudiante de la UTA."
)
START_REPLY = (
    "Hola, soy el asesor del CTT-FISEI, de la Facultad de Ingeniería en Sistemas de la Universidad Técnica de Ambato. "
    "Te ayudo a encontrar el curso que te sirve, con las fechas y los valores que publica el centro. "
    "Cuéntame qué te gustaría aprender o a qué te quieres dedicar."
)


async def _handle_incoming(
    *,
    telegram_chat_id: int,
    name: str,
    username: str,
    text: str,
    origin: str = "telegram",
    deliver: bool = True,
) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    conversation = db.get_or_create_conversation(telegram_chat_id, name, username, origin)
    db.add_message(conversation["id"], "user", text)
    await broadcast({"type": "message", "conversation_id": conversation["id"]})
    conversation = db.get_conversation(conversation["id"])
    if conversation["mode"] == "human":
        return conversation

    epoch = _epochs[conversation["id"]]
    folded = text.lower().strip()
    if folded in {"/start", "/help"}:
        result = {
            "reply": START_REPLY,
            "mood": "interesado",
            "intent": "info",
            "handover": False,
            "handover_reason": "",
            "summary": "Acaba de escribir.",
            "profile": conversation.get("profile") or {},
            "flag": False,
        }
        courses = []
    elif text == FILE_NOTE:
        result = {
            "reply": official_signup(conversation.get("courses") or []),
            "mood": "interesado",
            "intent": "inscripcion",
            "handover": False,
            "handover_reason": "",
            "summary": "Envió un archivo. Se le indicó el formulario oficial.",
            "profile": conversation.get("profile") or {},
            "flag": False,
        }
        courses = conversation.get("courses") or []
    elif text == "[Envió una nota de voz]":
        result = {
            "reply": VOICE_REPLY,
            "mood": conversation.get("mood") or "interesado",
            "intent": "info",
            "handover": False,
            "handover_reason": "",
            "summary": "Envió una nota de voz.",
            "profile": conversation.get("profile") or {},
            "flag": False,
        }
        courses = []
    else:
        if looks_incoherent(text):
            result = {
                "reply": clarify(conversation),
                "mood": "dudoso",
                "intent": "info",
                "handover": False,
                "handover_reason": "",
                "summary": "Envió un mensaje confuso. Se le pidió repetirlo en español.",
                "profile": conversation.get("profile") or {},
                "flag": False,
            }
            courses = conversation.get("courses") or []
        elif (precheck := guard.classify(text)):
            result = precheck
            courses = conversation.get("courses") or []
        else:
            if deliver and origin == "telegram":
                await typing(telegram_chat_id)
            history = db.recent_messages(conversation["id"], 8)
            result, courses = await answer(conversation, text, history)
            if guard.reply_offers_unauthorized(result.get("reply") or ""):
                result["reply"] = guard.SAFE_PRICE
                result["flag"] = True
                result["mood"] = "dudoso"
                result["intent"] = "objecion"
                result["summary"] = "La respuesta se corrigió para no ofrecer un precio no autorizado."

    async with _send_locks[conversation["id"]]:
        if _epochs[conversation["id"]] != epoch or db.get_conversation(conversation["id"])["mode"] != "ai":
            return db.get_conversation(conversation["id"])
        reply = result["reply"]
        handover = bool(result.get("handover"))
        profile = result.get("profile") or conversation.get("profile") or {}
        db.add_message(conversation["id"], "assistant", reply)
        updated = db.update_conversation(
            conversation["id"],
            mode="human" if handover else "ai",
            mood=result.get("mood") or "interesado",
            intent=result.get("intent") or "otro",
            summary=result.get("summary") or conversation.get("summary") or "",
            profile_json=json.dumps(profile, ensure_ascii=False),
            handover_reason=(result.get("handover_reason") or result.get("summary") or "") if handover else "",
            flagged=1 if conversation.get("flagged") or result.get("flag") else 0,
            courses_json=json.dumps(courses, ensure_ascii=False),
        )
        delivered = False
        if deliver and origin == "telegram":
            delivered = await send_message(telegram_chat_id, reply)
        await broadcast({"type": "message", "conversation_id": conversation["id"], "delivered": delivered})
        return updated


async def operator_message(conversation_id: int, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Escribe un mensaje.")
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        raise LookupError("Esa conversación no existe.")
    _epochs[conversation_id] += 1
    db.update_conversation(conversation_id, mode="human", handover_reason="El operador tomó el chat.")
    message = db.add_message(conversation_id, "operator", text)
    delivered = False
    if conversation.get("origin") == "telegram":
        async with _send_locks[conversation_id]:
            delivered = await send_message(conversation["telegram_chat_id"], text)
    await broadcast({"type": "message", "conversation_id": conversation_id, "delivered": delivered})
    return {"message": message, "delivered": delivered, "conversation": db.get_conversation(conversation_id)}


async def set_mode(conversation_id: int, mode: str) -> dict:
    if mode not in {"ai", "human"}:
        raise ValueError("Modo inválido.")
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        raise LookupError("Esa conversación no existe.")
    _epochs[conversation_id] += 1
    reason = "" if mode == "ai" else (conversation.get("handover_reason") or "El operador tomó el chat.")
    updated = db.update_conversation(conversation_id, mode=mode, handover_reason=reason)
    await broadcast({"type": "mode", "conversation_id": conversation_id, "mode": mode})
    return updated


async def handle_incoming(**kwargs):
    async with _chat_locks[kwargs["telegram_chat_id"]]:
        return await _handle_incoming(**kwargs)

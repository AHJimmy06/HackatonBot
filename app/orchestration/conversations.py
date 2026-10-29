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
from app.motor import engine as motor
from app.motor.config import MODES, MOTOR_MODES
from app.motor.taxonomy import SLOTS
from app.persistence import database as db
from app.realtime.hub import broadcast

log = logging.getLogger(__name__)

FILE_NOTE = "[Envió un archivo]"
VOICE_NOTE = "[Envió una nota de voz]"
OTHER_NOTE = "[Envió un sticker u otro contenido]"
VOICE_REPLY = (
    "Si me lo escribes lo leo enseguida. Cuéntame qué quieres aprender y si eres estudiante de la UTA."
)
START_REPLY = (
    "Hola, soy el asesor del CTT-FISEI, de la Facultad de Ingeniería en Sistemas de la Universidad Técnica de Ambato. "
    "Te ayudo a encontrar el curso que te sirve, con las fechas y los valores que publica el centro. "
    "Cuéntame qué te gustaría aprender o a qué te quieres dedicar."
)
ACTION_LABELS = {"temario": "Ver temario", "otra": "Otra opción", "humano": "Hablar con una persona",
                 "reiniciar": "Empezar de nuevo", "avisar": "Avísame cuando abra", "avisar_nuevos": "Avísame de cursos nuevos",
                 "flexible": "Ver lo que está abierto", "volver_ia": "Seguir con el asistente",
                 "inscribir": "Inscribirme", "calificar": "Calificó la recomendación"}


def engine_mode(conversation: dict) -> str:
    mode, _ = db.motor_get_state(conversation["id"])
    return mode if mode in MODES else motor.global_mode()


# ---------------------------------------------------------------- agente libre (pipeline original)

async def _legacy_turn(conversation: dict, text: str, origin: str, deliver: bool, telegram_chat_id: int):
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
    elif text in (VOICE_NOTE, OTHER_NOTE):
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
    return result, courses


async def _deliver_legacy(conversation, epoch, result, courses, telegram_chat_id, origin, deliver):
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


# ---------------------------------------------------------------- motor por pasos

async def motor_step(conversation: dict, fn: str, *args) -> motor.Result:
    """Un paso del motor fuera del event loop (las llamadas a Jev/LLM son síncronas)."""
    return await asyncio.to_thread(motor.run, conversation, fn, *args)


async def _deliver_motor(conversation, result, telegram_chat_id, origin, deliver, epoch=None, update_fields=True):
    cid = conversation["id"]
    async with _send_locks[cid]:
        current = db.get_conversation(cid)
        # una respuesta calculada antes de que el operador tomara el chat se descarta
        if epoch is not None and (_epochs[cid] != epoch or (current["mode"] != "ai" and not result.resumed)):
            return current
        replies = []
        for reply in result.replies:
            if guard.reply_offers_unauthorized(reply.text):  # capa final del proyecto, además de la guardia del motor
                reply = motor.Reply(guard.SAFE_PRICE, [])
                result.mood, result.intent, result.flag = "dudoso", "objecion", True
            db.add_message(cid, "assistant", motor.as_transcript(reply))
            replies.append(reply)
        updated = current
        if update_fields:
            fields = dict(
                mood=result.mood, intent=result.intent, summary=result.summary,
                profile_json=json.dumps(result.profile, ensure_ascii=False),
                courses_json=json.dumps(result.courses, ensure_ascii=False),
                flagged=1 if current.get("flagged") or result.flag else 0,
            )
            if result.handover:
                fields.update(mode="human", handover_reason=result.handover_reason)
            elif result.resumed:
                fields.update(mode="ai", handover_reason="")
            updated = db.update_conversation(cid, **fields)
        delivered = False
        if deliver and origin == "telegram":
            delivered = True
            for reply in replies:
                delivered = await send_message(telegram_chat_id, reply.text, reply.buttons, html=True) and delivered
        await broadcast({"type": "message", "conversation_id": cid, "delivered": delivered})
        return updated


def _motor_command(conversation: dict, text: str):
    """Comandos del motor en el chat: /modo para comparar desde el celular."""
    parts = text.split()
    if parts and parts[0].lower() == "/modo":
        arg = parts[1] if len(parts) > 1 else ""
        if arg not in MODES:
            opts = ", ".join(f"{k} ({v})" for k, v in MODES.items())
            return f"Modo actual: {engine_mode(conversation)}. Opciones: {opts}"
        db.motor_set_engine_mode(conversation["id"], arg)
        return f"Modo cambiado a {MODES[arg]}. Escribe /start para empezar de nuevo."
    return None


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
    # cédula, teléfono y correo no se guardan ni viajan a ninguna API
    text, had_pii = motor.redact(text)
    db.add_message(conversation["id"], "user", text)
    await broadcast({"type": "message", "conversation_id": conversation["id"]})
    conversation = db.get_conversation(conversation["id"])
    mode = engine_mode(conversation)

    if (reply := _motor_command(conversation, text)) is not None:
        result = motor.Result(replies=[motor.Reply(reply)])
        return await _deliver_motor(conversation, result, telegram_chat_id, origin, deliver, update_fields=False)

    if conversation["mode"] == "human":
        if mode in MOTOR_MODES:
            result = await motor_step(conversation, "waiting")
            return await _deliver_motor(conversation, result, telegram_chat_id, origin, deliver, update_fields=False)
        return conversation

    epoch = _epochs[conversation["id"]]
    if mode not in MOTOR_MODES:
        result, courses = await _legacy_turn(conversation, text, origin, deliver, telegram_chat_id)
        return await _deliver_legacy(conversation, epoch, result, courses, telegram_chat_id, origin, deliver)

    folded = text.lower().strip()
    if folded in {"/start", "/help", "/reiniciar"}:
        step = ("start", name)
    elif text == FILE_NOTE:
        step = ("on_media", "archivo")
    elif text in (VOICE_NOTE, OTHER_NOTE):
        step = ("on_media", "audio" if text == VOICE_NOTE else "otro")
    elif (precheck := guard.classify(text)) and precheck["kind"] == "humano":
        step = ("on_action", "humano")
    elif precheck:
        # capa 0 determinista: inyección o precio no autorizado, sin llamar a ninguna API
        step = ("layer0", precheck["reply"], precheck.get("summary", ""))
    else:
        step = ("on_text", text, looks_incoherent(text), had_pii)
    if deliver and origin == "telegram" and step[0] == "on_text":
        await typing(telegram_chat_id)
    result = await motor_step(conversation, *step)
    return await _deliver_motor(conversation, result, telegram_chat_id, origin, deliver, epoch)


def _callback_label(data: str) -> str:
    parts = data.split(":", 2)
    if parts[0] == "o" and len(parts) == 3:
        return SLOTS.get(parts[1], {}).get("opciones", {}).get(parts[2], (parts[2],))[0]
    if parts[0] == "a" and len(parts) >= 2:
        return ACTION_LABELS.get(parts[1], parts[1])
    return data


async def _handle_callback(*, telegram_chat_id: int, name: str, username: str, data: str,
                           origin: str = "telegram", deliver: bool = True) -> dict | None:
    conversation = db.get_or_create_conversation(telegram_chat_id, name, username, origin)
    if engine_mode(conversation) not in MOTOR_MODES:
        return conversation  # botón del motor en una conversación que ahora usa el agente libre
    parts = data.split(":", 2)
    if parts[0] == "o" and len(parts) == 3:
        step = ("on_option", parts[1], parts[2])
    elif parts[0] == "a" and len(parts) >= 2:
        step = ("on_action", parts[1], parts[2] if len(parts) > 2 else None)
    else:
        return conversation
    db.add_message(conversation["id"], "user", f"[Tocó: {_callback_label(data)}]")
    await broadcast({"type": "message", "conversation_id": conversation["id"]})
    conversation = db.get_conversation(conversation["id"])
    was_human = conversation["mode"] == "human"
    result = await motor_step(conversation, *step)
    if result.resumed:
        _epochs[conversation["id"]] += 1  # el usuario eligió volver con la IA
    elif was_human and not result.handover:
        return await _deliver_motor(conversation, result, telegram_chat_id, origin, deliver, update_fields=False)
    return await _deliver_motor(conversation, result, telegram_chat_id, origin, deliver, _epochs[conversation["id"]])


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
    if mode == "ai" and conversation["mode"] == "human" and engine_mode(conversation) in MOTOR_MODES:
        # el motor retoma desde el paso en que quedó y se lo dice al prospecto
        async with _chat_locks[conversation["telegram_chat_id"]]:
            result = await motor_step(updated, "resume")
            updated = await _deliver_motor(updated, result, conversation["telegram_chat_id"],
                                           conversation.get("origin") or "telegram", True, _epochs[conversation_id])
    return updated


async def set_engine_mode(conversation_id: int | None, mode: str) -> dict:
    """Modo del motor para una conversación, o el global (chats nuevos) si no se indica."""
    if mode not in MODES:
        raise ValueError("Modo de motor inválido.")
    if conversation_id is None:
        db.set_meta("motor_mode", mode)
        await broadcast({"type": "engine_mode", "mode": mode})
        return {"mode": mode}
    if not db.get_conversation(conversation_id):
        raise LookupError("Esa conversación no existe.")
    db.motor_set_engine_mode(conversation_id, mode)
    await broadcast({"type": "engine_mode", "conversation_id": conversation_id, "mode": mode})
    return {"conversation_id": conversation_id, "mode": mode}


async def handle_incoming(**kwargs):
    async with _chat_locks[kwargs["telegram_chat_id"]]:
        return await _handle_incoming(**kwargs)


async def handle_callback(**kwargs):
    async with _chat_locks[kwargs["telegram_chat_id"]]:
        return await _handle_callback(**kwargs)

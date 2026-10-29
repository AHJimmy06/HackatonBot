import json
import logging
import re

from openai import AsyncOpenAI

from app.catalog import retrieval
from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.inference.coherence import official_signup, plausible_name, polish
from app.inference.context import OUTPUT_TOKENS, build_messages

log = logging.getLogger(__name__)

MOODS = {"interesado", "dudoso", "frustrado", "caliente"}
INTENTS = {"info", "recomendacion", "objecion", "inscripcion", "humano", "inyeccion", "otro"}
PROFILES = {"estudiante_uta", "uta", "publico", "desconocido"}

def _merge_profile(old: dict, new: dict) -> dict:
    merged = dict(old or {})
    for key in ("nombre", "perfil", "interes", "objetivo"):
        value = str((new or {}).get(key) or "").strip()
        if not value or value.lower() in {"null", "none", "desconocido"}:
            continue
        if key == "perfil" and value not in PROFILES:
            continue
        if key == "nombre" and not plausible_name(value):
            continue
        merged[key] = value
    merged.setdefault("perfil", "desconocido")
    return merged


def _fallback(text: str, courses: list[dict], weak: bool, profile: dict) -> dict:
    folded = text.lower()
    wants_in = "inscrib" in folded
    if weak or not courses:
        reply = (
            "Cuéntame qué te gustaría lograr: programar, redes, Linux, ciberseguridad, electrónica o inteligencia artificial. "
            "Con eso te indico el curso del CTT que te sirve."
        )
        return {
            "reply": reply,
            "mood": "interesado",
            "intent": "info",
            "handover": False,
            "handover_reason": "",
            "summary": "Aún no define un interés claro.",
            "profile": profile,
            "flag": False,
        }
    course = courses[0]
    prices = course.get("prices") or {}
    hours = f" Son {course['hours']} horas." if course.get("hours") else ""
    modality = f" Modalidad {course['modality'].lower()}." if course.get("modality") else ""
    reply = (
        f"Por lo que me cuentas, el que más te encaja es {course['title']}. "
        f"{course.get('status_label')}.{hours}{modality} "
        f"Estudiante UTA {retrieval.money(prices.get('estudiante_uta'))}, "
        f"personal o graduados UTA {retrieval.money(prices.get('uta'))} "
        f"y público {retrieval.money(prices.get('publico'))}. "
        "¿Eres estudiante de la UTA o vienes por fuera?"
    )
    handover = False
    intent = "recomendacion"
    mood = "interesado"
    summary = f"Interés cercano a {course['title']}."
    if wants_in:
        handover = course.get("computed_status") == "inscripcion_abierta"
        intent = "inscripcion" if handover else "info"
        mood = "caliente" if handover else "dudoso"
        reply = official_signup([course])
        if handover:
            reply += " Un asesor del CTT puede orientarte en este mismo chat."
        summary = f"Quiere inscribirse en {course['title']}."
    return {
        "reply": reply,
        "mood": mood,
        "intent": intent,
        "handover": handover,
        "handover_reason": summary if handover else "",
        "summary": summary,
        "profile": profile,
        "flag": False,
    }


def _normalize(data: dict, profile: dict, courses: list[dict]) -> dict:
    mood = str(data.get("mood") or "").strip().lower()
    if mood not in MOODS:
        mood = "interesado"
    intent = str(data.get("intent") or "").strip().lower()
    if intent not in INTENTS:
        intent = "otro"
    reply = str(data.get("reply") or "").strip()
    handover = data.get("should_handover") is True or mood == "frustrado" or intent == "humano"
    if not reply:
        reply = "Cuéntame un poco más de lo que quieres aprender y te indico el curso del CTT que te sirve."
    if handover and "asesor" not in reply.lower() and "este mismo chat" not in reply.lower():
        reply = reply.rstrip() + " Sigo por aquí con un asesor del CTT, en este mismo chat."
    if len(reply) > 1200:
        reply = reply[:1200].rsplit(" ", 1)[0]
    known = {course["slug"] for course in courses}
    raw_ids = data.get("course_ids") or []
    if not isinstance(raw_ids, list):
        raw_ids = []
    return {
        "reply": reply,
        "mood": mood,
        "intent": intent,
        "handover": handover,
        "handover_reason": str(data.get("handover_reason") or "").strip()[:240],
        "summary": str(data.get("summary") or "").strip()[:240] or "Conversación en curso.",
        "profile": _merge_profile(profile, data.get("profile") if isinstance(data.get("profile"), dict) else {}),
        "flag": intent == "inyeccion",
        "course_ids": [item for item in raw_ids if item in known],
    }


def _parse_json(content: str) -> dict:
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


async def _complete(messages: list[dict]) -> str:
    client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=45)
    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0.4,
            max_tokens=OUTPUT_TOKENS,
            response_format={"type": "json_object"},
            messages=messages,
        )
    except Exception:
        log.warning("El proveedor no aceptó JSON forzado; reintento libre.")
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0.4,
            max_tokens=OUTPUT_TOKENS,
            messages=messages,
        )
    return response.choices[0].message.content or ""


async def answer(conversation: dict, text: str, history: list[dict]) -> tuple[dict, list[dict]]:
    profile = conversation.get("profile") or {}
    query = " ".join(
        part
        for part in (text, profile.get("interes") or "", profile.get("objetivo") or "")
        if part
    )
    courses, weak = retrieval.search(query)
    if not LLM_API_KEY:
        result = _fallback(text, courses, weak, profile)
    else:
        messages = build_messages(
            conversation, text, history,
            [retrieval.brief(course) for course in courses], weak,
        )
        try:
            data = _parse_json(await _complete(messages))
            result = _normalize(data, profile, courses)
        except Exception:
            log.exception("Fallo de inferencia")
            result = _fallback(text, courses, weak, profile)
    chosen_ids = set(result.pop("course_ids", []))
    chosen = [course for course in courses if course["slug"] in chosen_ids] or (courses if not weak else [])
    reply, changed = polish(result["reply"], text, chosen, result["profile"])
    result["reply"] = reply
    if changed:
        # A corrected model reply must not cancel an explicit request for a human.
        if result["intent"] == "humano" or result["mood"] == "frustrado":
            result["handover"] = True
            result["reply"] += " Un asesor del CTT te atiende en este mismo chat."
        else:
            result["handover"] = False
            result["handover_reason"] = ""
            result["mood"] = "dudoso"
            result["intent"] = "info"
    return result, _slim(chosen)


def _slim(courses: list[dict]) -> list[dict]:
    slim = []
    for course in courses[:3]:
        slim.append(
            {
                "slug": course["slug"],
                "title": course["title"],
                "status_label": course.get("status_label"),
                "computed_status": course.get("computed_status"),
                "hours": course.get("hours"),
                "modality": course.get("modality"),
                "prices": course.get("prices") or {},
                "url": course.get("url"),
            }
        )
    return slim

"""Clientes del LLM (API compatible con OpenAI) y de Jev, con medición de latencia."""
import json
import time

from app.motor.config import JEV_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, TYPESAFE_API_KEY

_openai = None
_jev = None


def llm_available() -> bool:
    return bool(LLM_API_KEY)


def jev_available() -> bool:
    return bool(TYPESAFE_API_KEY)


def openai_client():
    global _openai
    if _openai is None:
        import openai
        _openai = openai.OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=20, max_retries=1)
    return _openai


def jev_client():
    global _jev
    if _jev is None:
        from typesafe_sdk import TypeSafeClient
        _jev = TypeSafeClient(api_key=TYPESAFE_API_KEY)
    return _jev


def llm_json(system, user, schema, name="respuesta"):
    """Llamada al LLM con salida JSON validada por esquema. Devuelve (dict, ms)."""
    if not llm_available():
        raise RuntimeError("LLM sin configurar")
    t = time.perf_counter()
    resp = openai_client().chat.completions.create(
        model=LLM_MODEL,
        temperature=0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
    )
    return json.loads(resp.choices[0].message.content), int((time.perf_counter() - t) * 1000)


def llm_text(system, user):
    if not llm_available():
        raise RuntimeError("LLM sin configurar")
    t = time.perf_counter()
    resp = openai_client().chat.completions.create(
        model=LLM_MODEL,
        temperature=0.4,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip(), int((time.perf_counter() - t) * 1000)


def jev(state, questions):
    """Llamada a Jev. Devuelve (answers, ms)."""
    if not jev_available():
        raise RuntimeError("Jev sin configurar")
    t = time.perf_counter()
    resp = jev_client().system_one(state=state, questions=questions, model=JEV_MODEL, timeout=10)
    return resp.answers, int((time.perf_counter() - t) * 1000)


def obj_schema(props):
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}

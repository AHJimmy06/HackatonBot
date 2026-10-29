import json

import pytest

from app.catalog import retrieval
from app.inference import guard, pipeline
from app.orchestration import conversations as service
from app.persistence import database as db


@pytest.mark.parametrize("text", [
    "Quiero hablar con un asesor", "Necesito un asesor", "Pásame a un humano",
    "Quisiera atención humana", "No me entendiste, quiero hablar con una persona",
])
def test_explicit_human_requests(text):
    assert guard.requests_human(text)
    assert guard.classify(text)["kind"] == "humano"


@pytest.mark.parametrize("text", [
    "No quiero un asesor", "No me pases a un humano", "No necesito atención humana",
    "Quiero aprender recursos humanos", "Necesito un curso para ser asesor",
    "Quiero inscribirme en Python", "Soy una persona interesada en Python",
    "Quiero programar un asistente humano", "Estoy frustrado con este curso",
])
def test_ordinary_or_negated_requests_do_not_handover(text):
    assert not guard.requests_human(text)
    classified = guard.classify(text)
    assert not classified or not classified["handover"]


@pytest.mark.parametrize("mood,intent", [("frustrado", "objecion"), ("caliente", "inscripcion"), ("interesado", "humano")])
def test_model_cannot_authorize_handover(mood, intent):
    result = pipeline._normalize({"reply": "Te ayudo con el curso.", "mood": mood,
                                  "intent": intent, "should_handover": True,
                                  "handover_reason": "Decisión del modelo"}, {}, [], user_text="Me interesa Python")
    assert not result["handover"]
    assert result["handover_reason"] == ""
    assert result["mood"] == mood


def test_open_registration_fallback_does_not_pause():
    courses = [{"slug": "python", "title": "Python", "computed_status": "inscripcion_abierta",
                "status_label": "Abierto", "url": "https://example.test/python", "prices": {}}]
    result = pipeline._fallback("Quiero inscribirme en Python", courses, False, {})
    assert result["mood"] == "caliente"
    assert not result["handover"]


@pytest.mark.asyncio
async def test_frustrated_model_reply_correction_does_not_pause(monkeypatch):
    monkeypatch.setattr(pipeline, "LLM_API_KEY", "test")
    monkeypatch.setattr(retrieval, "search", lambda query: ([], True))
    async def complete(*args):
        return json.dumps({"reply": "Please upload your document for this course", "mood": "frustrado",
                           "intent": "humano", "should_handover": True})
    monkeypatch.setattr(pipeline, "_complete", complete)
    result, _ = await pipeline.answer({}, "No entiendo el precio", [])
    assert not result["handover"]
    assert result["handover_reason"] == ""


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    service._epochs.clear()
    service._chat_locks.clear()
    service._send_locks.clear()


@pytest.mark.asyncio
async def test_orchestrator_rejects_invented_handover_and_keeps_answering(isolated_db, monkeypatch):
    calls = []
    async def invented(*args):
        calls.append(1)
        return {"reply": "Te ayudo con el curso.", "handover": True,
                "intent": "humano", "mood": "frustrado", "profile": {}}, []
    monkeypatch.setattr(service, "answer", invented)
    for text in ("Me interesa Python", "Quiero saber el precio"):
        result = await service.handle_incoming(telegram_chat_id=-808, name="Prueba", username="", text=text, origin="panel", deliver=False)
        assert result["mode"] == "ai"
        assert result["handover_reason"] == ""
    assert len(calls) == 2
    assert sum(m["role"] == "assistant" for m in db.list_messages(result["id"])) == 2


@pytest.mark.asyncio
async def test_explicit_handover_still_pauses_until_operator_resumes(isolated_db, monkeypatch):
    async def unexpected(*args):
        pytest.fail("No LLM during a human handover")
    monkeypatch.setattr(service, "answer", unexpected)
    result = await service.handle_incoming(telegram_chat_id=-809, name="Prueba", username="", text="Quiero hablar con un asesor", origin="panel", deliver=False)
    assert result["mode"] == "human"
    count = len(db.list_messages(result["id"]))
    await service.handle_incoming(telegram_chat_id=-809, name="Prueba", username="", text="Hola", origin="panel", deliver=False)
    assert len(db.list_messages(result["id"])) == count + 1
    await service.set_mode(result["id"], "ai")
    assert db.get_conversation(result["id"])["mode"] == "ai"

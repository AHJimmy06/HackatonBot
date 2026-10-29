import asyncio
import json

import httpx
import pytest

from app.catalog import retrieval
from app.connectors import telegram
from app.inference import coherence, pipeline
from app.orchestration import conversations as service
from app.persistence import database as db


def course(slug="python", status="finalizado", title="Python avanzado"):
    return {"slug": slug, "title": title, "computed_status": status,
            "status_label": status, "url": f"https://example.test/{slug}", "prices": {}}


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    service._epochs.clear()
    service._chat_locks.clear()
    service._send_locks.clear()


@pytest.mark.parametrize("text", ["aaaaaa", "xkcd", "1234", "!!!"])
def test_incoherent_input(text):
    assert coherence.looks_incoherent(text)


@pytest.mark.parametrize("text", ["Hola", "Python", "CCNA", "Quiero aprender Linux", "/start"])
def test_meaningful_input(text):
    assert not coherence.looks_incoherent(text)


def test_name_validation_preserves_known_profile():
    assert coherence.plausible_name("Ana María Pérez")
    assert not coherence.plausible_name("quiero aprender Python")
    result = pipeline._merge_profile({"nombre": "Ana Pérez"}, {"nombre": "frontend"})
    assert result["nombre"] == "Ana Pérez"


def test_retrieval_keeps_current_alternative_within_budget(monkeypatch):
    courses = [course(f"python{i}") for i in range(3)] + [course("linux", "inscripcion_abierta", "Linux")]
    monkeypatch.setattr(retrieval, "list_courses", lambda: courses)
    selected, weak = retrieval.search("Python")
    assert len(selected) == 3
    assert selected[0]["slug"] == "python0"
    assert selected[-1]["slug"] == "linux"
    assert pipeline._slim(selected)[-1]["slug"] == "linux"
    assert not weak
    assert len(retrieval.search("Python", limit=1)[0]) == 1
    assert retrieval.search("Python", limit=0) == ([], True)


@pytest.mark.parametrize("status, expected", [
    ("finalizado", "ya no tiene inscripción abierta"),
    ("inscripcion_cerrada", "ya no tiene inscripción abierta"),
    ("por_abrir", "todavía no abre"),
    ("sin_fecha", "debe confirmarse"),
])
def test_official_signup_respects_course_status(status, expected):
    reply = coherence.official_signup([course(status=status)])
    assert expected in reply
    assert "https://example.test/python" in reply
    assert "no me envíes la cédula" in reply


def test_unknown_date_is_not_reported_closed():
    reply, changed = coherence.polish("Consulta el cupo con el CTT.", "", [course(status="sin_fecha")], {})
    assert not changed
    assert "ya no" not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_fallback_cannot_enroll_closed_course(monkeypatch, provider_fails):
    monkeypatch.setattr(pipeline, "LLM_API_KEY", "test" if provider_fails else "")
    monkeypatch.setattr(retrieval, "search", lambda query: ([course()], False))
    async def fail(*args):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(pipeline, "_complete", fail)
    result, selected = await pipeline.answer({}, "Quiero inscribirme en Python", [])
    assert "ya no tiene inscripción abierta" in result["reply"]
    assert not result["handover"]
    assert selected[0]["slug"] == "python"


@pytest.mark.asyncio
async def test_output_guard_preserves_human_request(monkeypatch):
    monkeypatch.setattr(pipeline, "LLM_API_KEY", "test")
    monkeypatch.setattr(retrieval, "search", lambda query: ([course()], False))
    async def complete(*args):
        return json.dumps({"reply": "Please upload your document for this course", "intent": "humano", "should_handover": True})
    monkeypatch.setattr(pipeline, "_complete", complete)
    result, _ = await pipeline.answer({}, "Quiero un asesor", [])
    assert result["handover"]
    assert "asesor del CTT" in result["reply"]


def test_string_false_is_not_handover():
    result = pipeline._normalize({"reply": "Hola", "should_handover": "false"}, {}, [])
    assert not result["handover"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text, expected", [("aaaaaa", "No me quedó claro"), (service.FILE_NOTE, "formulario oficial")])
async def test_confusion_and_files_bypass_llm(isolated_db, monkeypatch, text, expected):
    async def unexpected(*args):
        pytest.fail("These inputs must not call the LLM")
    monkeypatch.setattr(service, "answer", unexpected)
    result = await service.handle_incoming(telegram_chat_id=-123, name="Prueba", username="", text=text, origin="panel", deliver=False)
    assert result["mode"] == "ai"
    assert expected in db.list_messages(result["id"])[-1]["content"]


@pytest.mark.asyncio
async def test_pause_resume_invalidates_inflight_answer(isolated_db, monkeypatch):
    begun, release = asyncio.Event(), asyncio.Event()
    async def slow(*args):
        begun.set()
        await release.wait()
        return {"reply": "OBSOLETA", "profile": {}}, []
    monkeypatch.setattr(service, "answer", slow)
    task = asyncio.create_task(service.handle_incoming(telegram_chat_id=-42, name="Prueba", username="", text="Python", origin="panel", deliver=False))
    await asyncio.wait_for(begun.wait(), timeout=2)
    cid = db.list_conversations()[0]["id"]
    await service.set_mode(cid, "human")
    await service.set_mode(cid, "ai")
    release.set()
    await task
    assert db.get_conversation(cid)["mode"] == "ai"
    assert not any(m["content"] == "OBSOLETA" for m in db.list_messages(cid))


@pytest.mark.asyncio
async def test_private_files_are_routed_without_downloading(monkeypatch):
    seen = []
    async def receive(**kwargs):
        seen.append(kwargs)
    monkeypatch.setattr(service, "handle_incoming", receive)
    for media in ("photo", "document"):
        await telegram._on_update({"message": {"chat": {"id": 123, "type": "private"}, media: {"file_id": "unused"}, "caption": "ID"}})
    await telegram._on_update({"message": {"chat": {"id": -123, "type": "group"}, "text": "Hola"}})
    assert len(seen) == 2
    assert all(item["text"] == service.FILE_NOTE for item in seen)


@pytest.mark.asyncio
async def test_auth_origin_and_simulator_boundaries(isolated_db, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "test-password")
    # ASGITransport does not run startup: no polling, scraping or production database.
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/api/health")).status_code == 401
        client.auth = httpx.BasicAuth("admin", "test-password")
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/")).status_code == 200
        payload = {"telegram_chat_id": 123, "text": "/start"}
        assert (await client.post("/api/simulate", json=payload)).status_code == 422
        payload["telegram_chat_id"] = -123
        assert (await client.post("/api/simulate", json=payload, headers={"Origin": "https://foreign.test"})).status_code == 403
        assert (await client.post("/api/simulate", json=payload)).status_code == 200

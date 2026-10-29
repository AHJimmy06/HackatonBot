import asyncio
from datetime import date, timedelta

import pytest

from app.motor import analytics, engine, llm, writers
from app.orchestration import conversations as service
from app.persistence import database as db

pytestmark = [pytest.mark.motor, pytest.mark.asyncio]
CHAT = -900


def iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


def course(slug, title, area, nivel="principiante", formato="autodidacta", open_=True):
    return {
        "slug": slug, "url": f"https://example.test/{slug}.php", "title": title, "hours": 32,
        "listing_status": "ABIERTO", "computed_status": "inscripcion_abierta" if open_ else "finalizado",
        "description": f"Curso de {title}", "enrollment_start": iso(-5 if open_ else -90),
        "enrollment_end": iso(10 if open_ else -60), "course_start": iso(12 if open_ else -50),
        "course_end": iso(40 if open_ else -20), "schedule": "Sábados 08:00 a 13:00", "modality": "Virtual",
        "prices": {"estudiante_uta": 40.0, "uta": 50.0, "publico": 60.0}, "contents": "Clase 1: Intro\nClase 2: Práctica",
        "signup_url": "https://forms.example.test/inscripcion", "_tags": {
            "area": [area], "nivel": nivel, "objetivo": ["aprender_desde_cero", "certificacion"], "formato": formato},
    }


@pytest.fixture
def motor_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "motor.db")
    db.init_db()
    rows = [course("curso1", "Python desde cero", "programacion"),
            course("curso2", "Arduino práctico", "electronica", formato="presencial"),
            course("curso3", "Ethical Hacker", "ciberseguridad", open_=False)]
    db.replace_courses(rows)
    for row in rows:
        db.motor_set_tags(row["slug"], None, row["_tags"], "manual")
    # sin APIs: decide RulesDecider y redacta la plantilla
    monkeypatch.setattr(llm, "TYPESAFE_API_KEY", "")
    monkeypatch.setattr(llm, "LLM_API_KEY", "")
    monkeypatch.setattr(engine, "global_mode", lambda: "jev")
    service._epochs.clear()
    service._chat_locks.clear()
    service._send_locks.clear()


async def say(text):
    return await service.handle_incoming(telegram_chat_id=CHAT, name="Ana", username="", text=text,
                                         origin="panel", deliver=False)


async def press(data):
    return await service.handle_callback(telegram_chat_id=CHAT, name="Ana", username="", data=data,
                                         origin="panel", deliver=False)


def transcript(conv_id):
    return [m["content"] for m in db.list_messages(conv_id)]


async def test_full_funnel_feeds_analytics(motor_db):
    conv = await say("/start")
    assert any("asistente de cursos" in m for m in transcript(conv["id"]))
    for data in ("o:area:programacion", "o:experiencia:ninguna", "o:objetivo:aprender_desde_cero",
                 "o:formato:autodidacta"):
        conv = await press(data)
    assert conv["mood"] == "caliente"
    assert conv["courses"][0]["slug"] == "curso1"
    assert conv["profile"]["Interés"] == "Programación"
    assert any("Python desde cero" in m and "[📝 Inscribirme]" in m for m in transcript(conv["id"]))

    await press("a:inscribir:curso1")
    await press("a:calificar:si")
    analytics.mark_enrolled(conv["id"], "curso1")
    data = analytics.build(30)
    k = data["kpis"]
    assert (k["prospects"], k["recommendations"], k["signup_clicks"], k["enrolled"]) == (1, 1, 1, 1)
    assert k["revenue"] == 60.0 and k["csat"] == 100.0
    assert [f["n"] for f in data["funnel"]] == [1, 1, 1, 1, 1]


async def test_free_text_fills_several_steps_and_unmet_demand(motor_db):
    await say("/start")
    conv = await say("quiero algo de ciberseguridad")
    assert any("no hay cursos de Ciberseguridad" in m for m in transcript(conv["id"]))
    assert any("Avísame: Ethical Hacker" in m for m in transcript(conv["id"]))
    await press("a:avisar:curso3")
    data = analytics.build(30)
    assert data["areas"][0]["area"] == "ciberseguridad" and data["areas"][0]["without_offer"] == 1
    assert data["courses"][0]["notify_requests"] == 1


async def test_payment_question_hands_over_and_waits_once(motor_db):
    await say("/start")
    conv = await say("ya hice la transferencia, me dan factura?")
    assert conv["mode"] == "human"
    await say("hola?")
    await say("??")
    waiting = [m for m in transcript(conv["id"]) if "Ya avisé al equipo" in m]
    assert len(waiting) == 1


async def test_operator_takeover_discards_inflight_motor_answer(motor_db, monkeypatch):
    await say("/start")
    begun, release = asyncio.Event(), asyncio.Event()
    real = service.motor_step

    async def slow(conversation, fn, *args):
        if fn == "on_text":
            begun.set()
            await release.wait()
        return await real(conversation, fn, *args)

    monkeypatch.setattr(service, "motor_step", slow)
    task = asyncio.create_task(say("quiero programar"))
    await begun.wait()
    cid = db.list_conversations()[0]["id"]
    before = len(transcript(cid))
    await service.operator_message(cid, "Te atiendo yo")
    release.set()
    await task
    after = transcript(cid)
    assert db.get_conversation(cid)["mode"] == "human"
    assert after[-1] == "Te atiendo yo" and len(after) == before + 1


async def test_give_back_to_ai_resumes_pending_step(motor_db):
    await say("/start")
    conv = await press("a:humano")
    assert conv["mode"] == "human"
    resumed = await service.set_mode(conv["id"], "ai")
    assert resumed["mode"] == "ai"
    assert any("sigo yo" in m for m in transcript(conv["id"]))


async def test_personal_data_is_never_stored(motor_db):
    await say("/start")
    conv = await say("mi cédula es 1804567890 y mi correo ana@example.com")
    stored = " ".join(transcript(conv["id"]))
    assert "1804567890" not in stored and "ana@example.com" not in stored
    assert "No guardé ese dato" in stored


def test_output_guard_blocks_unpublished_facts():
    allowed = "Precio: $60.00 Público. Curso: Del 16 de noviembre de 2026"
    assert writers.guard("Cuesta $60", allowed) is None
    assert writers.guard("Te lo dejo en $10", allowed)
    assert writers.guard("Empieza el 1 de enero", allowed)
    assert writers.guard("¡Es gratis!", allowed)

"""Analítica de negocio y de calidad del chat, a partir de `motor_events` y las conversaciones.

Todo número sale de eventos registrados. Lo único estimado es el índice de satisfacción
implícito (penaliza frustración, malentendidos y calificaciones negativas) y se rotula así.
Las ventas reales no se ven desde el chat (el formulario es externo): se cuentan las que el
operador confirma con "Marcar inscrito"; el clic en "Inscribirme" es intención, no venta.
"""
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from app.motor.config import MODES
from app.motor.taxonomy import FAQ_INTENTS, SLOTS
from app.persistence import database as db

INTENT_LABELS = {"precio": "Precio", "fechas": "Fechas", "horario": "Horario", "modalidad": "Modalidad",
                 "certificado": "Certificado", "requisitos": "Requisitos", "inscripcion": "Cómo inscribirse",
                 "temario": "Temario", "duracion": "Duración", "instructor": "Instructor", "cupo": "Cupos",
                 "otra": "Otras preguntas"}
PRICE_KEY = {"estudiante": "estudiante_uta", "comunidad": "uta", "publico": "publico"}


def _ts(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _pct(part, total):
    return round(100 * part / total, 1) if total else None


def satisfaction_score(events):
    """Índice implícito 0-100 de una conversación (estimado)."""
    score = 100
    kinds = Counter(e["kind"] for e in events)
    score -= 30 * kinds["frustracion"]
    score -= 10 * kinds["no_entendio"]
    score -= 5 * max(kinds["fuera_de_tema"] - 1, 0)
    score -= 25 * sum(1 for e in events if e["kind"] == "calificacion" and e["value"] == "no")
    score += 10 * sum(1 for e in events if e["kind"] == "calificacion" and e["value"] == "si")
    return max(0, min(100, score))


def build(days: int = 30, include_tests: bool = True) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    raw = db.motor_analytics_raw(since)
    convs = [c for c in raw["conversations"] if include_tests or c["origin"] != "panel"]
    ids = {c["id"] for c in convs}
    events = [e for e in raw["events"] if e["conversation_id"] in ids]
    courses = {c["slug"]: {**c, "prices": json.loads(c.pop("prices_json") or "{}")} for c in raw["courses"]}
    by_conv = defaultdict(list)
    for e in events:
        by_conv[e["conversation_id"]].append(e)

    def has(cid, kind):
        return any(e["kind"] == kind for e in by_conv.get(cid, []))

    total = len(convs)
    profiled = sum(1 for c in convs if len({e["value"].split("=")[0] for e in by_conv.get(c["id"], [])
                                            if e["kind"] == "perfil"}) >= 2)
    recommended = sum(1 for c in convs if has(c["id"], "recomendado"))
    clicked = sum(1 for c in convs if has(c["id"], "clic_inscripcion"))
    enrolled_events = [e for e in events if e["kind"] == "inscrito"]
    enrolled = len({e["conversation_id"] for e in enrolled_events})
    revenue = round(sum(e["amount"] or 0 for e in enrolled_events), 2)
    handed = sum(1 for c in convs if has(c["id"], "derivacion"))
    frustrated = sum(1 for c in convs if has(c["id"], "frustracion") or c["mood"] == "frustrado")
    ratings = [e for e in events if e["kind"] == "calificacion"]
    thumbs_up = sum(1 for e in ratings if e["value"] == "si")
    implicit = [satisfaction_score(by_conv[c["id"]]) for c in convs if by_conv.get(c["id"])]

    # tiempo hasta la primera respuesta del operador tras una derivación
    waits = []
    msgs_by_conv = defaultdict(list)
    for m in raw["messages"]:
        msgs_by_conv[m["conversation_id"]].append(m)
    for e in events:
        if e["kind"] != "derivacion":
            continue
        start = _ts(e["created_at"])
        reply = next((_ts(m["created_at"]) for m in msgs_by_conv.get(e["conversation_id"], [])
                      if m["role"] == "operator" and _ts(m["created_at"]) and _ts(m["created_at"]) >= start), None)
        if start and reply:
            waits.append((reply - start).total_seconds() / 60)

    # cursos: demanda, recomendaciones, intención y ventas
    per_course = defaultdict(Counter)
    for e in events:
        if e["course_slug"]:
            per_course[e["course_slug"]][e["kind"]] += 1
    course_rows = []
    for slug, counts in per_course.items():
        info = courses.get(slug, {})
        course_rows.append({
            "slug": slug, "title": info.get("title", slug), "status": info.get("computed_status"),
            "recommended": counts["recomendado"] + counts["alternativa"], "signup_clicks": counts["clic_inscripcion"],
            "enrolled": counts["inscrito"], "notify_requests": counts["avisame"], "questions": counts["pregunta"],
            "revenue": round(sum(e["amount"] or 0 for e in enrolled_events if e["course_slug"] == slug), 2),
        })
    course_rows.sort(key=lambda r: (r["enrolled"], r["signup_clicks"], r["recommended"], r["notify_requests"]), reverse=True)

    # demanda por área (lo que la gente busca) contra oferta vigente
    area_demand = Counter(e["value"].split("=", 1)[1] for e in events
                          if e["kind"] == "perfil" and (e["value"] or "").startswith("area="))
    unmet = Counter(e["value"] for e in events if e["kind"] == "sin_oferta" and e["value"])
    areas = [{"area": a, "label": SLOTS["area"]["opciones"].get(a, (a,))[0], "interested": n, "without_offer": unmet[a]}
             for a, n in area_demand.most_common() if a != "no_se"]

    questions = Counter(e["value"] for e in events if e["kind"] == "pregunta" and e["value"] in FAQ_INTENTS)
    missing = Counter(e["value"] for e in events if e["kind"] == "dato_faltante")
    question_rows = [{"intent": k, "label": INTENT_LABELS.get(k, k), "asked": n, "missing": missing[k]}
                     for k, n in questions.most_common()]
    reasons = Counter(e["value"] for e in events if e["kind"] == "derivacion")

    # actividad diaria
    daily = defaultdict(Counter)
    for c in convs:
        if (d := _ts(c["created_at"])):
            daily[d.date().isoformat()]["conversations"] += 1
    for e in events:
        if e["kind"] in ("recomendado", "clic_inscripcion", "inscrito") and (d := _ts(e["created_at"])):
            daily[d.date().isoformat()][e["kind"]] += 1
    today = date.today()
    daily_rows = [{"date": (today - timedelta(days=i)).isoformat(),
                   **{k: daily[(today - timedelta(days=i)).isoformat()][k]
                      for k in ("conversations", "recomendado", "clic_inscripcion", "inscrito")}}
                  for i in range(min(days, 14) - 1, -1, -1)]

    # comparación por modo del motor
    latency = defaultdict(list)
    for d in raw["decisions"]:
        if d["ok"] and d["latency_ms"]:
            latency[d["engine_mode"]].append(d["latency_ms"])
    mode_rows = []
    for mode, label in MODES.items():
        ids = [c["id"] for c in convs if (c["engine_mode"] or "") == mode]
        if not ids:
            continue
        r = [e for cid in ids for e in by_conv.get(cid, []) if e["kind"] == "calificacion"]
        mode_rows.append({
            "mode": mode, "label": label, "conversations": len(ids),
            "recommend_rate": _pct(sum(has(i, "recomendado") for i in ids), len(ids)),
            "signup_rate": _pct(sum(has(i, "clic_inscripcion") for i in ids), len(ids)),
            "handoff_rate": _pct(sum(has(i, "derivacion") for i in ids), len(ids)),
            "frustration_rate": _pct(sum(has(i, "frustracion") for i in ids), len(ids)),
            "csat": _pct(sum(1 for e in r if e["value"] == "si"), len(r)),
            "avg_latency_ms": round(sum(latency[mode]) / len(latency[mode])) if latency[mode] else None,
        })

    # leads para seguimiento: intención sin venta confirmada
    sold = {(e["conversation_id"], e["course_slug"]) for e in enrolled_events}
    names = {c["id"]: c["user_name"] or c["username"] or f"Chat {c['id']}" for c in convs}
    leads = []
    for e in reversed(events):
        if e["kind"] in ("clic_inscripcion", "avisame") and (e["conversation_id"], e["course_slug"]) not in sold:
            leads.append({"conversation_id": e["conversation_id"], "name": names.get(e["conversation_id"], "—"),
                          "course_slug": e["course_slug"], "course": courses.get(e["course_slug"] or "", {}).get("title")
                          or ("Cursos nuevos" if e["value"] == "nuevos" else e["course_slug"]),
                          "kind": "Quiere inscribirse" if e["kind"] == "clic_inscripcion" else "Pidió aviso",
                          "at": e["created_at"]})
    seen, unique_leads = set(), []
    for lead in leads:
        key = (lead["conversation_id"], lead["course_slug"], lead["kind"])
        if key not in seen:
            seen.add(key)
            unique_leads.append(lead)

    data = {
        "period_days": days,
        "include_tests": include_tests,
        "test_conversations": sum(1 for c in convs if c["origin"] == "panel"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kpis": {
            "prospects": total,
            "recommendations": recommended,
            "signup_clicks": clicked,
            "enrolled": enrolled,
            "revenue": revenue,
            "conversion_rate": _pct(clicked, total),
            "close_rate": _pct(enrolled, clicked),
            "automation_rate": _pct(total - handed, total),
            "handoff_rate": _pct(handed, total),
            "frustration_rate": _pct(frustrated, total),
            "csat": _pct(thumbs_up, len(ratings)),
            "ratings": len(ratings),
            "satisfaction_index": round(sum(implicit) / len(implicit)) if implicit else None,
            "operator_wait_min": round(sum(waits) / len(waits), 1) if waits else None,
        },
        "funnel": [
            {"step": "Iniciaron conversación", "n": total},
            {"step": "Dieron su perfil", "n": profiled},
            {"step": "Recibieron recomendación", "n": recommended},
            {"step": "Tocaron «Inscribirme»", "n": clicked},
            {"step": "Inscritos confirmados", "n": enrolled},
        ],
        "quality": {
            "thumbs_up": thumbs_up, "thumbs_down": len(ratings) - thumbs_up,
            "not_understood": sum(1 for e in events if e["kind"] == "no_entendio"),
            "off_topic": sum(1 for e in events if e["kind"] == "fuera_de_tema"),
            "frustration_events": sum(1 for e in events if e["kind"] == "frustracion"),
            "blocked_attempts": sum(1 for e in events if e["kind"] == "capa0"),
            "moods": Counter(c["mood"] for c in convs),
        },
        "courses": course_rows,
        "areas": areas,
        "questions": question_rows,
        "handoff_reasons": [{"reason": k, "n": n} for k, n in reasons.most_common()],
        "daily": daily_rows,
        "by_mode": mode_rows,
        "leads": unique_leads[:25],
    }
    data["insights"] = insights(data)
    return data


def insights(d: dict) -> list[str]:
    """Lecturas accionables a partir de los números (reglas simples, sin IA)."""
    out = []
    k = d["kpis"]
    for a in d["areas"]:
        if a["without_offer"] >= 2:
            out.append(f"{a['without_offer']} prospectos buscaron {a['label']} y no había curso vigente: "
                       "evalúa abrir una edición o avisarles cuando abra.")
    notify = sorted((c for c in d["courses"] if c["notify_requests"]), key=lambda c: -c["notify_requests"])
    if notify:
        top = notify[0]
        out.append(f"«{top['title']}» acumula {top['notify_requests']} pedidos de aviso: hay demanda para reabrirlo.")
    for q in d["questions"]:
        if q["missing"] >= 2:
            out.append(f"Preguntaron {q['missing']} veces por «{q['label']}» sin dato publicado: agrégalo a la ficha.")
    if k["frustration_rate"] and k["frustration_rate"] >= 20:
        out.append(f"{k['frustration_rate']} % de las conversaciones mostró frustración: revisa las derivaciones.")
    if k["operator_wait_min"] and k["operator_wait_min"] > 10:
        out.append(f"Las personas esperan {k['operator_wait_min']} min en promedio a un asesor tras la derivación.")
    leads = [l for l in d["leads"] if l["kind"] == "Quiere inscribirse"]
    if leads:
        out.append(f"{len(leads)} prospectos tocaron «Inscribirme» y no figuran como inscritos: haz seguimiento.")
    modes = [m for m in d["by_mode"] if m["conversations"] >= 3 and m["signup_rate"] is not None]
    if len(modes) >= 2:
        best = max(modes, key=lambda m: m["signup_rate"])
        out.append(f"El modo «{best['label']}» es el que más convierte ({best['signup_rate']} % llega a inscribirse).")
    if not out:
        out.append("Aún no hay suficientes conversaciones para sacar conclusiones.")
    return out


def mark_enrolled(conversation_id: int, course_slug: str) -> dict:
    """El operador confirma una inscripción real; el monto sale de la tarifa publicada."""
    course = db.get_course(course_slug)
    if not course:
        raise LookupError("Ese curso no existe.")
    mode, state = db.motor_get_state(conversation_id)
    tarifa = (state.get("slots") or {}).get("tarifa")
    prices = course.get("prices") or {}
    amount = prices.get(PRICE_KEY.get(tarifa, "publico"))
    db.motor_event(conversation_id, mode, "inscrito", course_slug, tarifa or "publico", amount)
    db.motor_add_lead(conversation_id, course_slug, "inscrito")
    return {"conversation_id": conversation_id, "course_slug": course_slug, "amount": amount,
            "price_basis": tarifa or "publico (tarifa no indicada)"}

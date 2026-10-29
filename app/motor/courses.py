"""Catálogo visto por el motor: filas de `courses` + etiquetas internas (`motor_tags`).

El motor no raspa ni escribe cursos: lee lo que dejó `app/catalog/scraper.py` (o el
importador de snapshot) y solo agrega la clasificación propia.
"""
import hashlib
import json
import logging
import re
from datetime import date

from app.catalog.scraper import compute_status
from app.motor.llm import llm_available, llm_json, obj_schema
from app.motor.taxonomy import COURSE_TAGS, MULTI_TAGS
from app.persistence import database as db

log = logging.getLogger(__name__)

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
PRICE_LABELS = {"estudiante_uta": "Estudiantes UTA", "uta": "Graduados, docentes y personal administrativo UTA",
                "publico": "Público en general"}
TAG_INPUT_FIELDS = ("nombre", "descripcion_corta", "temario", "modalidad", "horario", "requisitos",
                    "duracion_horas", "objetivos")


def _spanish_date(iso):
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return f"{d.day} de {MONTHS[d.month - 1]} de {d.year}"


def _range(start, end):
    a, b = _spanish_date(start), _spanish_date(end)
    if a and b:
        return f"Del {a} al {b}"
    return a or b


def _lines(text):
    return [line.strip(" •-·\t") for line in (text or "").splitlines() if line.strip(" •-·\t")]


def to_engine(row, tags=None):
    """Fila de `courses` al formato que usan el decisor y los redactores."""
    prices = row.get("prices") or {}
    instructors = [line for line in _lines(row.get("instructors"))
                   if not re.match(r"(?i)^(comit[eé]|instructor)", line)]
    status = compute_status(row.get("enrollment_start"), row.get("enrollment_end"),
                            row.get("course_start"), row.get("course_end"))
    return {
        "id": row["slug"],
        "nombre": row.get("title"),
        "descripcion_corta": row.get("description") or None,
        "objetivos": _lines(row.get("objectives")),
        "temario": [line for line in _lines(row.get("contents")) if len(line) > 3][:40],
        "duracion_horas": row.get("hours"),
        "modalidad": row.get("modality") or None,
        "horario": row.get("schedule") or None,
        "lugar": row.get("place") or None,
        "cupo": row.get("capacity") or None,
        "requisitos": row.get("prerequisites") or None,
        "certificado": row.get("certificate") or None,
        "instructores": instructors,
        "fecha_curso_texto": _range(row.get("course_start"), row.get("course_end")),
        "fechas_inscripcion": _range(row.get("enrollment_start"), row.get("enrollment_end")),
        "inicio_inscripcion": row.get("enrollment_start"),
        "fin_inscripcion": row.get("enrollment_end"),
        "status": status,
        "precios": [{"publico": PRICE_LABELS.get(k, k), "precio": float(v), "moneda": "USD"}
                    for k, v in prices.items() if v is not None],
        "link_inscripcion": row.get("signup_url") or None,
        "fuente_url": row.get("url"),
        "estado": row.get("listing_status") or None,
        "tags": tags,
    }


def all_courses():
    tags = db.motor_tags()
    return [to_engine(row, (tags.get(row["slug"]) or {}).get("tags")) for row in db.list_courses()]


def get_course(slug):
    if not slug:
        return None
    row = db.get_course(slug)
    if not row:
        return None
    return to_engine(row, (db.motor_tags().get(slug) or {}).get("tags"))


VIGENT = {"inscripcion_abierta", "por_abrir"}


def is_open(course, today=None):
    """Se puede ofrecer: inscripción abierta o por abrir según las fechas publicadas,
    no según la etiqueta ABIERTO del sitio."""
    fin = course.get("fin_inscripcion")
    if not fin or course.get("status") not in VIGENT:
        return False
    try:
        return date.fromisoformat(fin) >= (today or date.today())
    except ValueError:
        return False


def opens_later(course):
    """Fecha (texto) en que abre la inscripción si todavía no abrió; None si ya está abierta."""
    if course.get("status") != "por_abrir":
        return None
    return _spanish_date(course.get("inicio_inscripcion"))


def open_courses():
    return [c for c in all_courses() if is_open(c)]


def brief(c):
    """Resumen corto de un curso para pasarlo a un decisor."""
    return {
        "id": c["id"], "nombre": c["nombre"], "etiquetas": c.get("tags"),
        "modalidad": c.get("modalidad"), "horas": c.get("duracion_horas"),
        "requisitos": c.get("requisitos"),
        "descripcion": (c.get("descripcion_corta") or "")[:300],
    }


def slim(course):
    """Formato que el panel muestra como 'cursos considerados'."""
    row = db.get_course(course["id"]) or {}
    return {
        "slug": course["id"], "title": course["nombre"],
        "status_label": row.get("status_label"), "computed_status": course.get("status"),
        "hours": course.get("duracion_horas"), "modality": course.get("modalidad"),
        "prices": row.get("prices") or {}, "url": course.get("fuente_url"),
    }


# ---------------------------------------------------------------- etiquetado

def _tag_schema():
    props = {}
    for tag, options in COURSE_TAGS.items():
        enum = {"type": "string", "enum": list(options)}
        props[tag] = {"type": "array", "items": enum} if tag in MULTI_TAGS else enum
    return obj_schema(props)


TAG_SYSTEM = (
    "Clasificas cursos de capacitación tecnológica con una taxonomía fija. Usa solo la información "
    "dada. 'area' y 'objetivo' admiten varias etiquetas: incluye todas las que apliquen de verdad. "
    "Si el nombre o la descripción mencionan preparación para una certificación (Certified, Certification, "
    "CCNA, PCEP, PCAP, Essentials de Cisco, GH-600), 'objetivo' DEBE incluir 'certificacion'. "
    "'nivel' se deduce de los requisitos/conocimientos previos. 'formato' se deduce de modalidad "
    "y horario. Significado de cada etiqueta:\n"
    + "\n".join(f"- {t}: " + "; ".join(f"{k} = {v}" for k, v in opts.items()) for t, opts in COURSE_TAGS.items())
)
CERT_RE = re.compile(r"certifi|ccna|pcep|pcap|gh-\d+", re.I)


def _digest(c):
    payload = {k: c.get(k) for k in TAG_INPUT_FIELDS}
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def apply_rules(c, tags):
    """Correcciones deterministas sobre lo que dijo el LLM, basadas en texto literal del curso."""
    if CERT_RE.search(c.get("nombre") or "") and "certificacion" not in tags["objetivo"]:
        tags["objetivo"].append("certificacion")
    return tags


def tag_course(c):
    payload = {k: c.get(k) for k in TAG_INPUT_FIELDS}
    tags, ms = llm_json(TAG_SYSTEM, json.dumps(payload, ensure_ascii=False), _tag_schema(), "etiquetas")
    tags = apply_rules(c, tags)
    db.motor_set_tags(c["id"], _digest(c), tags, "llm")
    db.motor_log_decision(None, None, "etiquetar", "llm", {"curso": c["id"]}, tags, None, ms, True)
    return tags


def tag_missing():
    """Etiqueta cursos nuevos o cambiados. Las etiquetas manuales se conservan."""
    if not llm_available():
        return 0
    known = db.motor_tags()
    done = 0
    for c in all_courses():
        prev = known.get(c["id"])
        if prev and (prev["source"] == "manual" or prev["hash"] == _digest(c)):
            continue
        try:
            tag_course(c)
            done += 1
        except Exception as exc:
            log.warning("No se pudo etiquetar %s: %s", c["id"], exc)
    return done


def set_tags(slug, tags):
    db.motor_set_tags(slug, None, tags, "manual")

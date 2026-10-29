"""Importa un snapshot de cursos (data/cursos.json de scraper_ctt.py) al esquema de `courses`.

Sirve cuando este equipo no llega al sitio del CTT pero un navegador sí: se captura el HTML,
se genera el JSON y se carga aquí. Nunca inventa campos: lo que falta queda vacío.
"""
import json
from pathlib import Path

from app.catalog.scraper import compute_status, parse_range
from app.config import ROOT
from app.persistence import database as db

SNAPSHOT = ROOT / "data" / "cursos.json"
PRICE_KEYS = (("estudiante", "estudiante_uta"), ("graduad", "uta"), ("docente", "uta"), ("general", "publico"))


def _prices(items):
    prices = {}
    for item in items or []:
        label = (item.get("publico") or "").lower()
        key = next((k for needle, k in PRICE_KEYS if needle in label), None)
        if key and key not in prices:
            prices[key] = item.get("precio")
    return prices


def to_row(c: dict) -> dict:
    enrollment_start, enrollment_end = parse_range(c.get("fechas_inscripcion") or "")
    course_start, course_end = parse_range(c.get("fecha_curso_texto") or "")
    slug = (c.get("fuente_url") or c["id"]).rsplit("/", 1)[-1].replace(".php", "")
    row = {
        "slug": slug,
        "url": c.get("fuente_url") or "",
        "title": c.get("nombre") or slug,
        "hours": c.get("duracion_horas"),
        "listing_status": c.get("estado") or "",
        "description": c.get("descripcion_corta") or "",
        "objectives": "\n".join(c.get("objetivos") or []),
        "place": c.get("lugar") or "",
        "instructors": "\n".join(c.get("instructores") or []),
        "enrollment_start": enrollment_start,
        # el snapshot ya trae la fecha de cierre calculada aunque el rango tenga otro formato
        "enrollment_end": enrollment_end or c.get("fin_inscripcion"),
        "course_start": course_start or c.get("fecha_inicio"),
        "course_end": course_end,
        "schedule": c.get("horario") or "",
        "modality": c.get("modalidad") or "",
        "capacity": c.get("cupo") or "",
        "prerequisites": c.get("requisitos") or "",
        "certificate": c.get("certificado") or "",
        "prices": _prices(c.get("precios")),
        "contents": "\n".join(c.get("temario") or []),
        "note": "",
        "signup_url": c.get("link_inscripcion") or "",
    }
    row["computed_status"] = compute_status(row["enrollment_start"], row["enrollment_end"],
                                            row["course_start"], row["course_end"])
    return row


def import_snapshot(path: Path = SNAPSHOT) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    count = db.replace_courses([to_row(c) for c in data["cursos"]])
    db.set_meta("catalog_updated_at", data.get("generado_en") or db.now())
    db.set_meta("catalog_error", "")
    db.set_meta("catalog_source", f"snapshot:{Path(path).name}")
    return count

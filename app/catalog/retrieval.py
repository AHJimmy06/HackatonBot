import re

from app.persistence.database import list_courses
from app.textutil import fold

STOPWORDS = {
    "de", "la", "el", "en", "y", "que", "un", "una", "para", "con", "por", "mi",
    "me", "se", "los", "las", "del", "al", "es", "soy", "quiero", "aprender",
    "curso", "cursos", "hola", "buenas", "buenos", "dias", "tardes", "noches",
    "como", "mas", "muy", "pero", "porque", "sobre", "este", "esta", "eso",
    "hay", "del", "le", "lo", "ya", "si", "no", "o", "u", "a",
}

SYNONYMS = {
    "programar": ["python", "javascript", "programacion"],
    "programacion": ["python", "javascript", "programacion"],
    "codigo": ["python", "javascript", "programacion"],
    "redes": ["redes", "ccna", "cisco"],
    "cisco": ["ccna", "redes", "cisco"],
    "ccna": ["ccna", "redes"],
    "linux": ["linux"],
    "hacking": ["ciber", "amenazas", "ethical"],
    "ciberseguridad": ["ciber", "amenazas", "ethical", "seguridad"],
    "seguridad": ["ciber", "amenazas", "seguridad", "proteccion"],
    "arduino": ["arduino", "electronica"],
    "electronica": ["arduino", "electronica"],
    "robotica": ["arduino", "automatizacion"],
    "ia": ["inteligencia", "artificial", "agentic", "github"],
    "inteligencia": ["inteligencia", "artificial", "agentic"],
    "artificial": ["inteligencia", "artificial", "agentic"],
    "agentes": ["agentic", "github", "inteligencia"],
    "datos": ["datos", "analitica", "python"],
    "plc": ["plc", "industrial", "automatizacion"],
    "industrial": ["industrial", "plc", "automatizacion"],
    "privacidad": ["proteccion", "datos", "ley"],
    "lopdp": ["proteccion", "datos", "ley"],
    "rrhh": ["recursos", "humanos"],
    "recursos": ["recursos", "humanos"],
    "python": ["python"],
    "javascript": ["javascript"],
    "almacen": ["almacenes", "distribucion"],
}

STATUS_BOOST = {
    "inscripcion_abierta": 8,
    "por_abrir": 6,
    "en_curso": 1,
    "inscripcion_cerrada": 0,
    "sin_fecha": 0,
    "finalizado": -4,
}


def _words(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]{2,}", fold(text))
    expanded: list[str] = []
    for word in raw:
        if word in STOPWORDS:
            continue
        expanded.append(word)
        expanded.extend(SYNONYMS.get(word, []))
    return expanded


def search(query: str, limit: int = 3) -> tuple[list[dict], bool]:
    courses = list_courses()
    words = _words(query)
    ranked = []
    for course in courses:
        haystack = {
            "title": fold(course["title"]),
            "description": fold(course.get("description") or ""),
            "objectives": fold(course.get("objectives") or ""),
            "contents": fold(course.get("contents") or ""),
        }
        score = STATUS_BOOST.get(course.get("computed_status"), 0)
        for word in words:
            if word in haystack["title"]:
                score += 6
            elif word in haystack["description"] or word in haystack["objectives"]:
                score += 3
            elif word in haystack["contents"]:
                score += 1
        course = dict(course)
        course["_score"] = score
        ranked.append(course)
    ranked.sort(key=lambda item: item["_score"], reverse=True)
    matched = [item for item in ranked if item["_score"] >= 5]
    weak = not matched
    chosen = (matched or ranked)[:limit]
    for item in chosen:
        item.pop("_score", None)
    return chosen, weak


def money(value) -> str:
    if value is None:
        return "no publicado"
    number = float(value)
    if number.is_integer():
        return f"${int(number)}"
    return f"${number:.2f}"


def brief(course: dict) -> str:
    prices = course.get("prices") or {}
    bits = [
        f"[{course['slug']}] {course['title']}",
        course.get("status_label") or course.get("computed_status") or "",
        f"{course['hours']} h" if course.get("hours") else "",
        course.get("modality") or "",
    ]
    line = " | ".join(bit for bit in bits if bit)
    price = (
        f"Precios oficiales: estudiante UTA {money(prices.get('estudiante_uta'))}, "
        f"graduados/docentes/administrativos UTA {money(prices.get('uta'))}, "
        f"público {money(prices.get('publico'))}."
    )
    dates = []
    if course.get("enrollment_start") and course.get("enrollment_end"):
        dates.append(f"Inscripción: {course['enrollment_start']} a {course['enrollment_end']}.")
    if course.get("course_start") and course.get("course_end"):
        dates.append(f"Curso: {course['course_start']} a {course['course_end']}.")
    if course.get("schedule"):
        dates.append(f"Horario: {course['schedule']}.")
    description = (course.get("description") or "").strip()
    if len(description) > 420:
        description = description[:420].rsplit(" ", 1)[0] + "…"
    objectives = (course.get("objectives") or "").strip()
    if len(objectives) > 280:
        objectives = objectives[:280].rsplit(" ", 1)[0] + "…"
    extra = " ".join(dates)
    prereq = course.get("prerequisites") or ""
    return "\n".join(
        part
        for part in (
            line,
            price,
            extra,
            f"Requisitos: {prereq}" if prereq else "",
            f"De qué trata: {description}" if description else "",
            f"Objetivos: {objectives}" if objectives else "",
            f"Ficha: {course['url']}",
        )
        if part
    )

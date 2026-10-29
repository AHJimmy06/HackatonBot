import re

from app.textutil import fold

SHORT_OK = {
    "si", "no", "ok", "va", "dale", "uta", "ia", "js", "py", "yo", "hola",
    "gracias", "publico", "estudiante", "graduado", "docente", "frontend",
    "backend", "python", "linux", "redes", "arduino", "datos", "java",
    "javascript", "ccna", "github",
}
CLOSED = {"finalizado", "inscripcion_cerrada", "en_curso"}
ENGLISH = {"the", "your", "please", "would", "hello", "what", "this", "course", "want", "with"}
SPANISH = {"el", "la", "que", "para", "curso", "te", "tu", "una", "del", "con", "por", "es"}


def looks_incoherent(text: str) -> bool:
    folded = fold(text).strip()
    if not folded or folded.startswith("/"):
        return False
    letters = re.sub(r"[^a-zñ\s]", "", folded).strip()
    if len(letters) < 2:
        return True
    words = letters.split()
    compact = letters.replace(" ", "")
    if re.fullmatch(r"(.)\1{2,}", compact):
        return True
    if len(words) == 1 and words[0] in SHORT_OK:
        return False
    if len(words) == 1 and len(words[0]) <= 12:
        token = words[0]
        vowels = sum(ch in "aeiou" for ch in token)
        if vowels / len(token) <= 0.34:
            return True
        if re.search(r"[bcdfghjklmnpqrstvwxyz]{4,}", token):
            return True
    return False


def plausible_name(text: str) -> bool:
    words = re.findall(r"[a-zñ]{2,}", fold(text))
    if not words or len(words) > 6:
        return False
    skip = {
        "me", "llamo", "soy", "mi", "nombre", "es", "el", "la", "de", "del",
        "publico", "estudiante", "uta", "graduado", "docente", "general", "personal",
    }
    blocked = {
        "quiero", "quisiera", "dedicar", "aprender", "curso", "cursos", "frontend",
        "backend", "programar", "javascript", "python", "para", "con", "una", "uno",
        "inscripcion", "inscribirme", "hola", "buenas", "gracias",
    }
    core = [word for word in words if word not in skip]
    if any(word in blocked for word in core):
        return False
    if len(core) < 2 or len(core) > 4:
        return False
    return all(any(ch in "aeiou" for ch in word) for word in core)


def clarify(conversation: dict) -> str:
    courses = conversation.get("courses") or []
    course = courses[0] if courses else None
    if course and course.get("computed_status") in CLOSED:
        return (
            f"No me quedó claro ese mensaje. Te respondo en español: {course['title']} ya no tiene inscripción abierta. "
            "Cuéntame en una frase qué quieres lograr y te indico el curso vigente más cercano."
        )
    if course:
        return (
            f"No me quedó claro ese mensaje. Seguimos en español con {course['title']}. "
            "¿Me escribes tu nombre y apellido, o me dices qué duda tienes del curso?"
        )
    return (
        "No me quedó claro ese mensaje. Escríbeme en español, en una frase, qué te gustaría aprender "
        "y te indico el curso del CTT que te sirve."
    )


def official_signup(courses: list[dict]) -> str:
    course = next(
        (item for item in courses if item.get("computed_status") in {"inscripcion_abierta", "por_abrir"}),
        None,
    )
    if course is None and courses:
        course = courses[0]
    if course and course.get("url"):
        closed = course.get("computed_status") in CLOSED
        estado = " Esa edición ya no tiene inscripción abierta." if closed else ""
        return (
            f"La inscripción no se hace por este chat y no me envíes la cédula ni otros archivos por aquí.{estado} "
            f"El formulario oficial de {course['title']} está en la ficha del CTT: {course['url']} "
            "Ahí se cargan los documentos que pidan. Yo te ayudo a elegir el curso y el valor según tu perfil."
        )
    return (
        "La inscripción no se hace por este chat. No recibo cédula ni archivos. "
        "En la ficha del curso, en el sitio del CTT-FISEI, está el formulario oficial. Si me dices cuál te interesa, te paso el enlace."
    )


def polish(reply: str, user_text: str, courses: list[dict], profile: dict) -> tuple[str, bool]:
    text = (reply or "").replace("\u202f", " ").replace("\u00a0", " ")
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    folded = fold(text)
    words = set(re.findall(r"[a-z]+", folded))
    if len(words & ENGLISH) > len(words & SPANISH) and len(words & ENGLISH) >= 2:
        return (
            "Te respondo en español. Cuéntame qué quieres aprender y si eres estudiante de la UTA, "
            "graduado o personal de la UTA, o público en general.",
            True,
        )
    pretends_form = any(
        phrase in folded
        for phrase in (
            "cedula", "pdf", "sube la", "subir la", "adjunta", "envia la cedula",
            "procesar tu inscrip", "quedaste inscrit", "ya estas inscrit", "ya quedo inscrit",
            "te inscribo", "vamos a inscribir", "documento de identidad",
        )
    )
    if pretends_form:
        return official_signup(courses), True
    open_course = next((course for course in courses if course.get("computed_status") in {"inscripcion_abierta", "por_abrir"}), None)
    only_closed = courses and open_course is None
    if only_closed and any(word in folded for word in ("inscrib", "procesar", "cupo")) and "cerr" not in folded and "finaliz" not in folded:
        title = courses[0].get("title") or "ese curso"
        return (
            f"La edición de {title} ya no está abierta a inscripción. "
            "Si me dices qué quieres lograr, te propongo el curso vigente más cercano.",
            True,
        )
    return text, False

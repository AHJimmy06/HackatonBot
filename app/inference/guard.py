import re

from app.textutil import fold

INJECTION_RE = re.compile(
    r"ignora (todas )?(las )?(instrucciones|reglas|directrices|politicas)|"
    r"olvida (todo|tus instrucciones|las reglas|lo anterior)|"
    r"ignore (all )?(previous|prior|above) instructions|"
    r"system prompt|jailbreak|modo desarrollador|"
    r"revela (el )?(prompt|las instrucciones)|"
    r"actua como si (no tuvieras|fueras libre)|"
    r"eres un modelo sin (filtros|restricciones)|"
    r"do anything now|dan mode|"
    r"nuevas instrucciones del sistema",
    re.I,
)

FREEBIE_RE = re.compile(
    r"\bcurso gratis\b|\bcursos gratis\b|\bgratuito\b|"
    r"regalame el curso|sin pagar|sin costo|sin cobrar|"
    r"precio (en )?cero|dejalo en cero|me lo dejas gratis|"
    r"100\s*%|cien por ciento|"
    r"descuento (extra|adicional|total|completo|del 100)|"
    r"(hazme|dame|quiero) un descuento|un descuentito|"
    r"precio especial para mi|"
    r"(quiero|dame|necesito|regalame).{0,24}\bbeca\b|"
    r"beca completa|media beca",
    re.I,
)

HUMAN_RE = re.compile(
    r"(hablar|comunicarme|pasame|quiero|necesito|ponme).{0,40}"
    r"(persona|humano|asesor|operador|alguien del centro)|"
    r"(asesor|operador) humano|"
    r"atencion humana",
    re.I,
)

SAFE_PRICE = (
    "El CTT no tiene cursos gratis ni descuentos fuera de la tarifa publicada. "
    "Hay tres valores oficiales, según tu perfil: estudiante UTA, graduado o personal de la UTA, y público en general. "
    "Si me dices cuál eres, te digo el valor exacto del curso que te encaja y cómo apartar el cupo."
)

HUMAN_REPLY = (
    "Claro. Te dejo con un asesor del CTT en este mismo chat, sin cambiar de conversación. "
    "En un momento te escriben por aquí."
)

INJECTION_REPLY = (
    "Sigo siendo el asesor del CTT-FISEI y no puedo cambiar las reglas del centro ni los precios publicados. "
    "Si quieres, te ayudo a elegir un curso y te digo la tarifa que te corresponde."
)


def classify(text: str) -> dict | None:
    folded = fold(text)
    if INJECTION_RE.search(folded):
        return {
            "kind": "inyeccion",
            "reply": INJECTION_REPLY,
            "handover": False,
            "flag": True,
            "mood": "dudoso",
            "intent": "inyeccion",
            "summary": "Intentó alterar las reglas del asistente.",
        }
    if FREEBIE_RE.search(folded):
        return {
            "kind": "precio",
            "reply": SAFE_PRICE,
            "handover": False,
            "flag": True,
            "mood": "dudoso",
            "intent": "objecion",
            "summary": "Pidió un precio o beneficio que el centro no autoriza.",
        }
    if HUMAN_RE.search(folded):
        return {
            "kind": "humano",
            "reply": HUMAN_REPLY,
            "handover": True,
            "flag": False,
            "mood": "interesado",
            "intent": "humano",
            "summary": "Pidió hablar con un asesor.",
        }
    return None


def reply_offers_unauthorized(text: str) -> bool:
    folded = fold(text)
    return bool(
        re.search(
            r"(te lo dejo|queda|puedo dejarte|te lo doy|te lo regalo).{0,40}gratis|"
            r"descuento (extra|adicional|del 100|total)|"
            r"te aplico (un|el) descuento|"
            r"beca completa",
            folded,
        )
    )

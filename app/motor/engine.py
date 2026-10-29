"""Máquina de pasos del motor. No envía ni guarda mensajes: devuelve un Result.

Pasos de la recomendación:
  1. Entender  — el decisor convierte texto libre en una opción tipada (o se usa el botón).
  2. Perfilar  — objetivo, área, experiencia y formato; se extrae todo lo que el usuario diga.
  3. Preguntar — el decisor elige la pregunta que más distingue entre cursos vigentes.
  4. Elegir    — el decisor elige el curso con su confianza; si es baja, pregunta más.
  5. Presentar — tarjeta armada por código desde el catálogo; el LLM solo redacta alrededor.
Paso a humano cuando lo pide, en pagos/descuentos o si no se entiende tres veces.
"""
import re
from dataclasses import dataclass, field

from app.motor import courses as catalog
from app.motor import deciders, writers
from app.motor.config import CONFIDENCE_THRESHOLD, DEFAULT_MODE, HUMAN_THRESHOLD, MODES, OVERRIDE_THRESHOLD
from app.motor.taxonomy import PROFILE_SLOTS, SLOTS
from app.persistence import database as db

MAX_INPUT = 600  # lo que pasa de aquí no aporta para decidir y solo encarece
# cédula/RUC/teléfono ecuatorianos (10-13 dígitos) y correos
PII_RE = re.compile(r"\b\d{10}(?:\d{3})?\b|\b09\d{8}\b|[\w.+-]+@[\w-]+\.[\w.]+")
HUMAN_BUTTON = {"text": "🙋 Hablar con una persona", "data": "a:humano"}
BACK_TO_BOT = {"text": "🤖 Seguir con el asistente", "data": "a:volver_ia"}
PROFILE_LABELS = {"objetivo": "Objetivo", "area": "Interés", "experiencia": "Experiencia",
                  "formato": "Formato", "tarifa": "Tarifa"}


@dataclass
class Reply:
    text: str
    buttons: list = field(default_factory=list)  # filas de {"text", "data"} o {"text", "url"}


@dataclass
class Result:
    replies: list
    handover: bool = False
    handover_reason: str = ""
    resumed: bool = False
    flag: bool = False
    mood: str = "interesado"
    intent: str = "info"
    summary: str = ""
    profile: dict = field(default_factory=dict)
    courses: list = field(default_factory=list)


def new_state():
    return {"slots": {}, "pending": None, "rec": None, "shown": [], "misses": 0}


def option_buttons(slot):
    opts = SLOTS[slot]["opciones"]
    btns = [{"text": label, "data": f"o:{slot}:{key}"} for key, (label, _) in opts.items()]
    return [btns[i:i + 2] for i in range(0, len(btns), 2)]


def plain(text):
    return re.sub(r"<[^>]+>", "", text or "")


def redact(text):
    """Quita datos personales antes de guardarlos o mandarlos a una API."""
    clean = PII_RE.sub("[dato personal oculto]", text)
    return clean, clean != text


def as_transcript(reply):
    """Texto del mensaje tal como se guarda para el panel: incluye las opciones mostradas."""
    labels = [b["text"] for row in reply.buttons for b in row]
    text = plain(reply.text)
    return text + ("\n" + " · ".join(f"[{label}]" for label in labels) if labels else "")


def global_mode():
    mode = db.get_meta("motor_mode", DEFAULT_MODE)
    return mode if mode in MODES else DEFAULT_MODE


class Session:
    def __init__(self, conv):
        self.conv = conv
        mode, state = db.motor_get_state(conv["id"])
        self.mode = mode if mode in MODES else global_mode()
        self.state = state or new_state()
        roles = {"user": "usuario", "assistant": "asistente", "operator": "operador"}
        self.history = [f"{roles.get(m['role'], m['role'])}: {plain(m['content'])[:200]}"
                        for m in db.recent_messages(conv["id"], 8)]
        self.decider = deciders.for_mode(self.mode, conv["id"])
        self.writer = writers.for_mode(self.mode, conv["id"], self.history)
        self.out = []
        self.result = Result(replies=self.out, intent="info")

    @property
    def human(self):
        return self.conv.get("mode") == "human"

    @property
    def slots(self):
        return self.state["slots"]

    # ------------------------------------------------------------ utilidades

    def say(self, text, buttons=None):
        self.out.append(Reply(text, buttons or []))

    def event(self, kind, course=None, value=None, amount=None):
        db.motor_event(self.conv["id"], self.mode, kind, course, value, amount)

    def ask(self, slot):
        self.state["pending"] = slot
        self.say(self.writer.ask(slot, slots=self.slots), option_buttons(slot))

    def reprompt(self, slot):
        if slot:
            self.say(SLOTS[slot]["pregunta"], option_buttons(slot))

    def candidates(self):
        found = catalog.open_courses()
        area = self.slots.get("area")
        if area and area != "no_se" and not self.slots.get("area_flexible"):
            found = [c for c in found if area in ((c.get("tags") or {}).get("area") or [])]
        return [c for c in found if c["id"] not in self.state["shown"]]

    def finish(self):
        """Estado guardado y señales que el panel muestra (termómetro, resumen, perfil, cursos)."""
        db.motor_save_state(self.conv["id"], self.mode, self.state)
        r = self.result
        r.profile = {PROFILE_LABELS[s]: SLOTS[s]["opciones"][v][0]
                     for s, v in self.slots.items() if s in PROFILE_LABELS and v in SLOTS[s]["opciones"]}
        rec = catalog.get_course(self.state.get("rec"))
        ids = list(dict.fromkeys(([rec["id"]] if rec else []) + self.state.get("shown", [])))[:3]
        r.courses = [catalog.slim(c) for c in map(catalog.get_course, ids) if c]
        parts = [f"{SLOTS[s]['opciones'][v][1]}" for s, v in self.slots.items()
                 if s in PROFILE_SLOTS and v in SLOTS[s]["opciones"]]
        summary = "; ".join(parts) if parts else "Aún sin perfil."
        if rec:
            summary += f". Recomendado: {rec['nombre']}."
        r.summary = f"[{MODES[self.mode]}] {summary}"
        if r.handover and r.mood == "interesado":
            r.mood = "dudoso"
        # un lead caliente no se enfría por un paso neutro (calificar, ver temario)
        if r.mood == "interesado" and self.conv.get("mood") == "caliente":
            r.mood = "caliente"
        return r

    # ------------------------------------------------------------ flujo

    def start(self, name=None, force=False):
        if self.human and not force:
            # /start no debe quitarle la conversación al operador sin que el usuario lo elija
            return self.say(self.writer.human_active(), [[BACK_TO_BOT]])
        self.state = new_state()
        self.event("inicio")
        self.say(self.writer.greet(name))
        self.advance()

    def refine(self):
        """Llegó un dato nuevo después de recomendar: se recalcula con todos los cursos vigentes."""
        previous = self.state.get("rec")
        self.state.update(rec=None, shown=[], pending=None)
        self.advance(previous=previous)

    def advance(self, previous=None):
        remaining = [s for s in PROFILE_SLOTS if s not in self.slots]
        cands = self.candidates()
        if not cands:
            return self.no_match()
        area_known = "area" in self.slots
        if not remaining or (len(cands) == 1 and area_known):
            return self.recommend(cands, previous=previous)
        step = self.decider.next_step(slots=self.slots, remaining=remaining, candidates=cands)
        nxt = step.get("siguiente")
        if nxt == "recomendar" and area_known:
            return self.recommend(cands, remaining, previous=previous)
        # sin área no se recomienda: es lo que más cambia la recomendación
        self.ask(nxt if nxt in remaining else remaining[0])

    def recommend(self, cands, remaining=(), previous=None):
        pick = self.decider.pick(slots=self.slots, candidates=cands)
        chosen = next((c for c in cands if c["id"] == pick.get("curso")), None)
        if chosen is None:
            chosen = deciders.rank(cands, self.slots)[0]
        if (pick.get("confianza") or 0) < CONFIDENCE_THRESHOLD and remaining:
            return self.ask(remaining[0])
        self.state["rec"] = chosen["id"]
        self.state["shown"].append(chosen["id"])
        if chosen["id"] == previous:
            # mismo resultado: se confirma sin repetir la tarjeta completa
            self.result.mood, self.result.intent = "caliente", "recomendacion"
            buttons = [[{"text": "📝 Inscribirme", "data": f"a:inscribir:{chosen['id']}"}]] if chosen.get("link_inscripcion") else []
            return self.say(f"Con eso, <b>{writers.e(chosen['nombre'])}</b> sigue siendo tu mejor opción 👌",
                            buttons + [[{"text": "🔄 Otra opción", "data": "a:otra"}]])
        self.show(chosen)

    def better_closed(self, course):
        """Curso cerrado que encaja mejor que el recomendado: se ofrece como 'avísame'."""
        mine = deciders.score(course, self.slots)
        closed = [c for c in catalog.all_courses() if not catalog.is_open(c) and c.get("tags")]
        best = max(closed, key=lambda c: deciders.score(c, self.slots), default=None)
        return best if best and deciders.score(best, self.slots) > mine else None

    def show(self, course, alternative=False):
        self.state["pending"] = None
        self.say(self.writer.recommend(course, alternative=alternative, slots=self.slots))
        buttons = []
        if course.get("link_inscripcion"):
            # botón con callback (no de link) para poder medir la intención de inscripción
            buttons.append([{"text": "📝 Inscribirme", "data": f"a:inscribir:{course['id']}"}])
        buttons.append([{"text": "📚 Ver temario", "data": "a:temario"}, {"text": "🔄 Otra opción", "data": "a:otra"}])
        buttons.append([{"text": "👍 Me sirvió", "data": "a:calificar:si"}, {"text": "👎 No me sirvió", "data": "a:calificar:no"}])
        closed = self.better_closed(course)
        if closed:
            buttons.append([{"text": f"🔔 Avísame: {closed['nombre'][:40]}", "data": f"a:avisar:{closed['id']}"}])
        buttons.append([HUMAN_BUTTON, {"text": "↩️ Empezar de nuevo", "data": "a:reiniciar"}])
        card = writers.course_card(course, self.slots.get("tarifa"))
        nivel = (course.get("tags") or {}).get("nivel")
        if self.slots.get("experiencia") == "ninguna" and nivel in ("intermedio", "avanzado"):
            card += f"\n⚠️ <b>Ojo:</b> este curso pide bases previas. {writers.e(course.get('requisitos') or '')}"
        if (opens := catalog.opens_later(course)):
            card += f"\n🗓 <b>Las inscripciones abren el {writers.e(opens)}.</b>"
        if closed:
            card += (f"\n🔔 <b>{writers.e(closed['nombre'])}</b> encaja mejor contigo, pero sus inscripciones "
                     "cerraron. Si quieres te avisamos cuando abra.")
        if course.get("link_inscripcion"):
            card += f"\n<i>{writers.ONE_FORM_NOTE}</i>"
        self.say(card, buttons)
        db.motor_add_lead(self.conv["id"], course["id"], "recomendado")
        self.event("alternativa" if alternative else "recomendado", course["id"])
        self.result.mood = "caliente"
        self.result.intent = "recomendacion"

    def no_match(self):
        area = self.slots.get("area")
        if self.state["shown"]:
            return self.say(self.writer.no_more(), [[HUMAN_BUTTON, {"text": "↩️ Empezar de nuevo", "data": "a:reiniciar"}]])
        if not catalog.open_courses() or not area or area == "no_se" or self.slots.get("area_flexible"):
            return self.say(self.writer.nothing_open(),
                            [[{"text": "🔔 Avísame de cursos nuevos", "data": "a:avisar_nuevos"}, HUMAN_BUTTON]])
        closed = [c for c in catalog.all_courses()
                  if not catalog.is_open(c) and area in ((c.get("tags") or {}).get("area") or [])][:3]
        self.event("sin_oferta", value=area)
        label = SLOTS["area"]["opciones"].get(area, (area,))[0]
        buttons = [[{"text": f"🔔 Avísame: {c['nombre'][:40]}", "data": f"a:avisar:{c['id']}"}] for c in closed]
        buttons.append([{"text": "👀 Ver lo que está abierto", "data": "a:flexible"}])
        self.say(self.writer.no_match(label, closed), buttons)
        self.result.mood = "dudoso"

    def handoff(self, reason, announce=True):
        self.state["waiting_notified"] = False
        self.result.handover = True
        self.result.handover_reason = reason
        self.result.intent = "humano"
        self.event("derivacion", value=reason)
        if announce:
            self.say(self.writer.handoff())

    def answer_question(self, text, intent):
        course = catalog.get_course(self.state.get("rec"))
        self.result.intent = intent
        if intent == "otra" and course is None:
            self.event("dato_faltante", value="otra")
            return self.say(self.writer.answer("otra", None, text), [[HUMAN_BUTTON]])
        if course is None:
            self.event("pregunta", value=intent)
            return self.say(self.writer.need_course())
        facts = writers.facts_for(intent, course, self.slots.get("tarifa"))
        self.event("pregunta", course["id"], intent)
        if facts is None:
            self.event("dato_faltante", course["id"], intent)
        if intent in ("inscripcion", "fechas") and not catalog.is_open(course):
            facts = (facts + "\n" if facts else "") + "Ojo: las inscripciones de este curso ya cerraron."
        reply = self.writer.answer(intent, facts, question=text, course=course)
        self.say(reply, [] if facts or self.mode == "llm" else [[HUMAN_BUTTON]])
        if intent == "inscripcion":
            self.result.mood = "caliente"

    # ------------------------------------------------------------ entradas

    def on_option(self, slot, value):
        if self.human:
            return self.waiting()
        if slot not in SLOTS or value not in SLOTS[slot]["opciones"]:
            return  # botón viejo o manipulado
        if self.slots.get(slot) not in (None, value) and slot in PROFILE_SLOTS:
            # botón de una pregunta anterior: se toma como cambio de opinión
            self.slots[slot] = value
            return self.restart_recommendation([slot])
        self.set_slot(slot, value)

    def set_slot(self, slot, value):
        self.slots[slot] = value
        self.event("perfil", value=f"{slot}={value}")
        self.state["pending"] = None
        self.state["misses"] = 0
        if slot == "tarifa" and self.state.get("rec"):
            course = catalog.get_course(self.state["rec"])
            return self.say(self.writer.answer("precio", writers.facts_for("precio", course, value), course=course))
        if self.state.get("rec") and slot in PROFILE_SLOTS:
            return self.refine()
        self.advance()

    def on_action(self, action, arg=None):
        if action == "volver_ia":
            return self.resume() if self.human else None
        if action == "reiniciar":
            return self.start(self.conv.get("user_name"))
        if self.human:
            return self.waiting()
        if action == "humano":
            return self.handoff("Pidió hablar con una persona.")
        if action == "inscribir":
            course = catalog.get_course(arg or self.state.get("rec"))
            if not course or not course.get("link_inscripcion"):
                return self.say(self.writer.answer("inscripcion", None), [[HUMAN_BUTTON]])
            self.event("clic_inscripcion", course["id"])
            db.motor_add_lead(self.conv["id"], course["id"], "intencion_inscripcion")
            self.result.mood, self.result.intent = "caliente", "inscripcion"
            note = f" Las inscripciones abren el {catalog.opens_later(course)}." if catalog.opens_later(course) else ""
            return self.say(f"¡Excelente! 🎉 Este es el formulario oficial para <b>{writers.e(course['nombre'])}</b>.{note}\n"
                            f"<i>{writers.ONE_FORM_NOTE}</i>",
                            [[{"text": "📝 Abrir formulario oficial", "url": course["link_inscripcion"]}], [HUMAN_BUTTON]])
        if action == "calificar":
            useful = arg == "si"
            self.event("calificacion", self.state.get("rec"), "si" if useful else "no")
            if useful:
                return self.say("¡Gracias por contarnos! 🙌")
            self.result.mood = "dudoso"
            return self.say("Gracias por decírnoslo. ¿Quieres que te atienda una persona del CTT o buscamos otra opción?",
                            [[HUMAN_BUTTON, {"text": "🔄 Otra opción", "data": "a:otra"}]])
        if action == "temario":
            course = catalog.get_course(self.state.get("rec"))
            return self.say(writers.TemplateWriter().answer("temario", writers.facts_for("temario", course), course=course))
        if action == "otra":
            cands = self.candidates()
            if not cands:
                return self.no_match()
            nxt = deciders.rank(cands, self.slots)[0]
            self.state["rec"] = nxt["id"]
            self.state["shown"].append(nxt["id"])
            return self.show(nxt, alternative=True)
        if action == "avisar" and arg:
            course = catalog.get_course(arg)
            if course is None:
                return self.say("Ese curso ya no está en el catálogo. Te muestro lo que hay abierto:",
                                [[{"text": "👀 Ver lo que está abierto", "data": "a:flexible"}]])
            db.motor_add_lead(self.conv["id"], arg, "avisar_apertura")
            self.event("avisame", arg)
            self.result.mood = "caliente"
            return self.say(f"Listo ✅ Te anotamos para avisarte cuando abra <b>{writers.e(course['nombre'])}</b>.",
                            [[{"text": "👀 Ver lo que está abierto", "data": "a:flexible"}]])
        if action == "avisar_nuevos":
            db.motor_add_lead(self.conv["id"], None, "avisar_nuevos")
            self.event("avisame", value="nuevos")
            return self.say("Listo ✅ Te avisamos cuando abra un curso nuevo.")
        if action == "flexible":
            self.slots["area_flexible"] = True
            return self.advance()

    def on_text(self, text, incoherent=False, had_pii=False):
        """`text` ya viene sin datos personales (ver `redact`)."""
        if self.human:
            return self.waiting()
        if not text:
            return
        if had_pii:
            self.say(self.writer.pii_warning())
        slot = self.state.get("pending")
        if incoherent:
            # la capa 0 ya lo detectó sin gastar una llamada: cuenta como "no entendí"
            return self.not_understood(slot)
        extract = [s for s in PROFILE_SLOTS + ["tarifa"] if s != slot]
        u = self.decider.understand(text=text[:MAX_INPUT], slot=slot, history=self.history[-4:], open_slots=extract)
        intent = u.get("intencion") or "otra"
        self.result.intent = intent
        if (u.get("humano") or 0) >= HUMAN_THRESHOLD:
            self.result.mood = "frustrado"
            self.event("frustracion", value=f"{u.get('humano'):.2f}")
            return self.handoff("Pidió una persona o mostró frustración.")
        if intent == "pago_descuento":
            self.say(self.writer.payment())
            return self.handoff("Consulta de pagos, becas o descuentos.", announce=False)
        if intent == "fuera_de_tema":
            self.state["off_topic"] = self.state.get("off_topic", 0) + 1
            self.event("fuera_de_tema")
            self.say(self.writer.off_topic(), [[HUMAN_BUTTON]] if self.state["off_topic"] >= 2 else [])
            self.result.mood = "dudoso"
            return self.reprompt(slot)
        if intent == "catalogo":
            # antes que extraer datos: "¿tienen java?" no es elegir programación
            self.say(self.writer.catalog_list(catalog.open_courses()))
            return self.reprompt(slot)

        # datos dados sin preguntar ("nunca he programado") y cambios de opinión ("mejor electrónica")
        learned, changed = self.apply_extras(u.get("extras") or {})
        opcion, conf = u.get("opcion"), u.get("confianza") or 0
        asked = intent in writers.INTENT_LABEL  # además de dar datos, hizo una pregunta concreta
        if slot and opcion in SLOTS[slot]["opciones"] and conf >= CONFIDENCE_THRESHOLD:
            if asked:
                self.answer_question(text, intent)
            return self.set_slot(slot, opcion)
        if changed:
            return self.restart_recommendation(changed)
        if learned:
            self.state["misses"] = 0
            if asked:
                self.answer_question(text, intent)
            if self.state.get("rec") and any(s in PROFILE_SLOTS for s in learned):
                return self.refine()
            return self.advance()

        if intent in ("saludo", "agradecimiento"):
            self.say(self.writer.smalltalk(intent))
            return self.reprompt(slot)
        if not slot or opcion == "pregunta" or intent != "otra":
            self.answer_question(text, intent)
            return self.reprompt(slot)
        self.not_understood(slot)

    def layer0(self, reply, summary=""):
        """La capa 0 del proyecto (regex de inyección/precio) ya respondió: se marca y se retoma el paso."""
        self.say(reply)
        self.event("capa0", value=summary[:120])
        self.result.flag = True
        self.result.mood = "dudoso"
        self.result.intent = "objecion"
        self.reprompt(self.state.get("pending"))

    def not_understood(self, slot):
        """Re-pregunta, luego ofrece una persona, luego deriva."""
        self.state["misses"] = self.state.get("misses", 0) + 1
        self.result.mood = "dudoso"
        self.event("no_entendio")
        if self.state["misses"] >= 3:
            return self.handoff("El asistente no logró entender al prospecto.")
        if not slot:
            return self.say("No te entendí bien 🙈. ¿Me lo escribes de otra forma?",
                            [[HUMAN_BUTTON]] if self.state["misses"] >= 2 else [])
        buttons = option_buttons(slot)
        if self.state["misses"] == 2:
            buttons = buttons + [[HUMAN_BUTTON]]
        self.say(self.writer.not_understood(slot), buttons)

    def apply_extras(self, extras):
        """Aplica lo que el decisor extrajo. Devuelve (slots nuevos, slots cambiados)."""
        learned, changed = [], []
        for s, v in extras.items():
            if s not in SLOTS or v.get("opcion") not in SLOTS[s]["opciones"]:
                continue
            current = self.slots.get(s)
            if current is None and v["confianza"] >= CONFIDENCE_THRESHOLD:
                self.slots[s] = v["opcion"]
                learned.append(s)
                self.event("perfil", value=f"{s}={v['opcion']}")
            elif current not in (None, v["opcion"]) and v["confianza"] >= OVERRIDE_THRESHOLD:
                self.slots[s] = v["opcion"]
                changed.append(s)
                self.event("cambio_opinion", value=f"{s}={v['opcion']}")
        return learned, changed

    def restart_recommendation(self, changed):
        labels = ", ".join(SLOTS[s]["opciones"][self.slots[s]][0] for s in changed)
        self.say(f"Listo, lo tomo en cuenta: <b>{writers.e(labels)}</b>. Busco de nuevo 🔎")
        self.state.update(rec=None, shown=[], pending=None, misses=0)
        self.slots.pop("area_flexible", None)
        self.advance()

    def waiting(self):
        """Mensaje del usuario mientras atiende una persona: la IA no responde, solo avisa una vez."""
        if self.state.get("waiting_notified"):
            return
        self.state["waiting_notified"] = True
        self.say(self.writer.waiting_human(), [[BACK_TO_BOT]])

    def on_media(self, kind):
        if self.human:
            return self.waiting()
        if kind == "archivo":
            self.say(self.writer.pii_warning())
        else:
            self.say(self.writer.media())
        self.reprompt(self.state.get("pending"))

    def resume(self):
        """La IA retoma (lo pidió el operador o el usuario) desde el paso en que quedó."""
        self.conv["mode"] = "ai"
        self.result.resumed = True
        self.event("retoma_ia")
        self.state["waiting_notified"] = False
        self.say(self.writer.back_to_bot())
        slot = self.state.get("pending")
        if slot:
            self.ask(slot)
        elif self.state.get("rec"):
            self.say("¿Te ayudo con algo más de este curso o buscamos otra opción?",
                     [[{"text": "📚 Ver temario", "data": "a:temario"}, {"text": "🔄 Otra opción", "data": "a:otra"}],
                      [{"text": "↩️ Empezar de nuevo", "data": "a:reiniciar"}]])
        else:
            self.advance()


def run(conv, fn, *args, **kwargs) -> Result:
    """Ejecuta un paso sobre la conversación y devuelve respuestas + señales para el panel."""
    s = Session(conv)
    getattr(s, fn)(*args, **kwargs)
    return s.finish()

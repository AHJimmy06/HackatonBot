"""Decisores: interpretan al usuario, eligen la próxima pregunta y el curso.

Los tres implementan la misma interfaz, así el motor puede alternarlos por modo:
- JevDecider: decisiones tipadas con probabilidad (Choice / Noul).
- LLMDecider: el mismo trabajo con OpenAI y salida JSON.
- RulesDecider: palabras clave y puntaje por etiquetas; respaldo si una API falla.
"""
import json
import unicodedata

from app.persistence import database as db

from app.motor.llm import jev, llm_json, obj_schema
from app.motor.taxonomy import (FAQ_INTENTS, FAQ_KEYWORDS, HUMAN_KEYWORDS, KEYWORDS, PROFILE_SLOTS,
                       SLOT_PURPOSE, SLOTS)

EXTRA_OPTIONS = {
    "pregunta": "En lugar de responder, hace una pregunta o pide información",
    "otro": "No encaja con ninguna opción o no se entiende",
}


def norm(text):
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return f" {text} "


def slot_options(slot):
    return {k: desc for k, (_, desc) in SLOTS[slot]["opciones"].items()}


def describe_profile(slots):
    """Perfil en palabras, para que el decisor no dependa de nuestras claves internas."""
    parts = []
    for slot, value in slots.items():
        if slot in SLOTS and value in SLOTS[slot]["opciones"]:
            parts.append(f"{SLOT_PURPOSE.get(slot, slot)}: {SLOTS[slot]['opciones'][value][1]}")
    return parts or ["sin datos todavía"]


# ---------------------------------------------------------------- puntaje por etiquetas

def score(course, slots):
    t = course.get("tags") or {}
    s = 0
    area = slots.get("area")
    if area and area != "no_se" and not slots.get("area_flexible"):
        s += 3 if area in t.get("area", []) else -3
    obj = slots.get("objetivo")
    if obj and obj != "explorar" and obj in t.get("objetivo", []):
        s += 2
    exp, nivel = slots.get("experiencia"), t.get("nivel")
    if exp in ("ninguna", "basica") and nivel == "principiante":
        s += 2
    elif exp == "intermedia" and nivel in ("intermedio", "avanzado"):
        s += 2
    elif exp == "ninguna" and nivel in ("intermedio", "avanzado"):
        s -= 2
    fmt = slots.get("formato")
    if fmt and fmt != "cualquiera":
        s += 1 if t.get("formato") in (fmt, "hibrido") else -1
    return s


def rank(courses, slots):
    return sorted(courses, key=lambda c: score(c, slots), reverse=True)


# ---------------------------------------------------------------- base

class Decider:
    name = "base"

    def __init__(self, conv_id=None, mode=None):
        self.conv_id, self.mode = conv_id, mode

    def _log(self, task, input_, output, confidence, ms, ok=True, provider=None):
        if input_.get("candidates") and isinstance(input_["candidates"][0], dict):
            input_ = {**input_, "candidates": [c["id"] for c in input_["candidates"]]}
        db.motor_log_decision(self.conv_id, self.mode, task, provider or self.name, input_, output, confidence, ms, ok)

    def _safe(self, task, input_, fn):
        """Ejecuta la decisión; si la API falla, responde RulesDecider y queda registrado."""
        try:
            out, ms = fn()
            self._log(task, input_, out, out.get("confianza"), ms)
            return out
        except Exception as exc:
            self._log(task, input_, {"error": str(exc)[:300]}, None, 0, ok=False)
            fallback = RulesDecider(self.conv_id, self.mode)
            return getattr(fallback, task)(**input_)


class RulesDecider(Decider):
    name = "reglas"

    def _timed(self, task, input_, out):
        self._log(task, input_, out, out.get("confianza"), 0)
        return out

    def understand(self, text, slot=None, history=None, open_slots=()):
        t = norm(text)
        humano = 0.9 if any(k in t for k in HUMAN_KEYWORDS) else 0.05
        extras = {}
        for s in open_slots:
            if s == slot:
                continue
            hits = {o: sum(norm(k).strip() in t for k in kws) for o, kws in KEYWORDS[s].items()}
            best = max(hits, key=hits.get)
            if hits[best]:
                extras[s] = {"opcion": best, "confianza": 0.7}
        intent = next((i for i, kws in FAQ_KEYWORDS.items() if any(norm(k).strip() in t for k in kws)), "otra")
        opcion, conf = None, 0.0
        if slot:
            hits = {o: sum(norm(k).strip() in t for k in kws) for o, kws in KEYWORDS[slot].items()}
            best = max(hits, key=hits.get)
            if hits[best]:
                opcion, conf = best, 0.7
            elif "?" in text or intent != "otra":
                opcion, conf = "pregunta", 0.6
            else:
                opcion, conf = "otro", 0.3
        return self._timed("understand", {"text": text, "slot": slot, "open_slots": list(open_slots)},
                           {"opcion": opcion, "confianza": conf, "intencion": intent, "humano": humano,
                            "extras": extras})

    def next_step(self, slots, remaining, candidates):
        if len(candidates) <= 1 or not remaining:
            out = {"siguiente": "recomendar", "confianza": 0.8}
        else:
            out = {"siguiente": next(s for s in PROFILE_SLOTS if s in remaining), "confianza": 0.8}
        return self._timed("next_step", {"slots": slots, "remaining": remaining,
                                         "candidates": [c["id"] for c in candidates]}, out)

    def pick(self, slots, candidates):
        ranked = rank(candidates, slots)
        scores = [score(c, slots) for c in ranked]
        unique_top = len(scores) == 1 or scores[0] > scores[1]
        out = {"curso": ranked[0]["id"], "confianza": 0.8 if unique_top else 0.5,
               "probabilidades": {c["id"]: s for c, s in zip(ranked, scores)}}
        return self._timed("pick", {"slots": slots, "candidates": [c["id"] for c in candidates]}, out)


class JevDecider(Decider):
    name = "jev"

    def understand(self, text, slot=None, history=None, open_slots=()):
        from typesafe_sdk import Choice, Noul

        def run():
            extra_slots = [s for s in open_slots if s != slot]
            questions = {
                "intencion": Choice(
                    instructions="Si el mensaje del usuario contiene una pregunta sobre un curso, ¿sobre qué es?",
                    criteria=FAQ_INTENTS),
                "humano": Noul(
                    instructions="¿El usuario pide hablar con una persona, un asesor humano, "
                                 "o muestra frustración con el bot?"),
            }
            if slot:
                questions["opcion"] = Choice(
                    instructions=f"El bot preguntó: '{SLOTS[slot]['pregunta']}'. "
                                 "¿Qué opción expresa la respuesta del usuario?",
                    criteria={**slot_options(slot), **EXTRA_OPTIONS})
            for s in extra_slots:
                questions[f"extra_{s}"] = Choice(
                    instructions=f"¿El mensaje del usuario dice algo sobre {SLOT_PURPOSE.get(s, s)}?",
                    criteria={**slot_options(s), "no_menciona": "El mensaje no dice nada sobre esto"})
            state = {"mensaje_del_usuario": text, "conversacion_reciente": history or []}
            answers, ms = jev(state, questions)
            out = {"intencion": answers["intencion"].choice, "humano": answers["humano"].noul,
                   "opcion": None, "confianza": answers["intencion"].confidence,
                   "extras": {s: {"opcion": answers[f"extra_{s}"].choice,
                                  "confianza": answers[f"extra_{s}"].confidence}
                              for s in extra_slots if answers[f"extra_{s}"].choice != "no_menciona"}}
            if slot:
                out["opcion"], out["confianza"] = answers["opcion"].choice, answers["opcion"].confidence
            return out, ms

        return self._safe("understand", {"text": text, "slot": slot, "history": history,
                                         "open_slots": list(open_slots)}, run)

    def next_step(self, slots, remaining, candidates):
        from typesafe_sdk import Choice
        from app.motor.courses import brief

        def run():
            criteria = {s: f"Preguntar {SLOT_PURPOSE[s]}" for s in remaining}
            criteria["recomendar"] = "Ya hay información suficiente para elegir un curso con confianza"
            answers, ms = jev(
                {"perfil_del_usuario": describe_profile(slots),
                 "cursos_candidatos": [brief(c) for c in candidates]},
                {"siguiente": Choice(
                    instructions="Eres el árbol de decisión de un asesor de cursos. Elige la próxima pregunta "
                                 "que mejor ayude a distinguir cuál de los cursos candidatos conviene al usuario, "
                                 "o 'recomendar' si ya basta. Pregunta solo lo que cambie la recomendación.",
                    criteria=criteria)})
            a = answers["siguiente"]
            return {"siguiente": a.choice, "confianza": a.confidence}, ms

        return self._safe("next_step", {"slots": slots, "remaining": remaining, "candidates": candidates}, run)

    def pick(self, slots, candidates):
        from typesafe_sdk import Choice
        from app.motor.courses import brief

        def run():
            criteria = {c["id"]: json.dumps(brief(c), ensure_ascii=False) for c in candidates}
            answers, ms = jev(
                {"perfil_del_usuario": describe_profile(slots)},
                {"curso": Choice(instructions="¿Cuál de estos cursos es el más adecuado para este usuario?",
                                 criteria=criteria)})
            a = answers["curso"]
            return {"curso": a.choice, "confianza": a.confidence, "probabilidades": a.probabilities}, ms

        return self._safe("pick", {"slots": slots, "candidates": candidates}, run)


class LLMDecider(Decider):
    name = "llm"
    SYSTEM = ("Eres el módulo de decisión de un asesor de cursos del CTT-FISEI (UTA, Ecuador). "
              "Respondes solo con el JSON pedido. 'confianza' va de 0 a 1 y refleja tu certeza real.")

    def understand(self, text, slot=None, history=None, open_slots=()):
        def run():
            extra_slots = [s for s in open_slots if s != slot]
            props = {
                "intencion": {"type": "string", "enum": list(FAQ_INTENTS)},
                "pide_persona": {"type": "number",
                                 "description": "Probabilidad 0-1 de que el usuario PIDA explícitamente hablar con "
                                                "una persona/asesor humano o esté molesto con el bot. 0 si no lo pide."},
                "confianza": {"type": "number"},
            }
            for s in extra_slots:
                props[f"extra_{s}"] = {"type": "string", "enum": list(slot_options(s)) + ["no_menciona"],
                                       "description": f"Lo que el mensaje dice sobre {SLOT_PURPOSE.get(s, s)}"}
            prompt = {"mensaje_del_usuario": text, "conversacion_reciente": history or [],
                      "intenciones_posibles": FAQ_INTENTS,
                      "opciones_extra": {f"extra_{s}": slot_options(s) for s in extra_slots}}
            if slot:
                props["opcion"] = {"type": "string", "enum": list(slot_options(slot)) + list(EXTRA_OPTIONS)}
                prompt["pregunta_del_bot"] = SLOTS[slot]["pregunta"]
                prompt["opciones"] = {**slot_options(slot), **EXTRA_OPTIONS}
                prompt["tarea"] = "Elige la opción que expresa la respuesta del usuario; 'confianza' es sobre esa opción."
            else:
                prompt["tarea"] = "Clasifica la intención del mensaje; 'confianza' es sobre la intención."
            out, ms = llm_json(self.SYSTEM, json.dumps(prompt, ensure_ascii=False), obj_schema(props), "entender")
            out.setdefault("opcion", None)
            out["humano"] = out.pop("pide_persona")
            out["extras"] = {s: {"opcion": out.pop(f"extra_{s}"), "confianza": 0.8} for s in extra_slots}
            out["extras"] = {s: v for s, v in out["extras"].items() if v["opcion"] != "no_menciona"}
            return out, ms

        return self._safe("understand", {"text": text, "slot": slot, "history": history,
                                         "open_slots": list(open_slots)}, run)

    def next_step(self, slots, remaining, candidates):
        from app.motor.courses import brief

        def run():
            options = {s: f"Preguntar {SLOT_PURPOSE[s]}" for s in remaining}
            options["recomendar"] = "Ya hay información suficiente para elegir un curso con confianza"
            prompt = {"perfil_del_usuario": describe_profile(slots),
                      "cursos_candidatos": [brief(c) for c in candidates],
                      "opciones": options,
                      "tarea": "Elige la próxima pregunta que mejor distinga cuál curso conviene, o 'recomendar'. "
                               "Pregunta solo lo que cambie la recomendación."}
            props = {"siguiente": {"type": "string", "enum": list(options)}, "confianza": {"type": "number"}}
            return llm_json(self.SYSTEM, json.dumps(prompt, ensure_ascii=False), obj_schema(props), "siguiente")

        return self._safe("next_step", {"slots": slots, "remaining": remaining, "candidates": candidates}, run)

    def pick(self, slots, candidates):
        from app.motor.courses import brief

        def run():
            prompt = {"perfil_del_usuario": describe_profile(slots),
                      "cursos_candidatos": [brief(c) for c in candidates],
                      "tarea": "Elige el curso más adecuado para este usuario."}
            props = {"curso": {"type": "string", "enum": [c["id"] for c in candidates]},
                     "confianza": {"type": "number"}}
            out, ms = llm_json(self.SYSTEM, json.dumps(prompt, ensure_ascii=False), obj_schema(props), "curso")
            out["probabilidades"] = {}
            return out, ms

        return self._safe("pick", {"slots": slots, "candidates": candidates}, run)


def for_mode(mode, conv_id=None):
    from app.motor.llm import jev_available, llm_available
    if mode == "llm":
        cls = LLMDecider if llm_available() else RulesDecider
    else:
        cls = JevDecider if jev_available() else RulesDecider
    return cls(conv_id, mode)

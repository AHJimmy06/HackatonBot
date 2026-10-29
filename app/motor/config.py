"""Configuración del motor. Reutiliza el LLM del proyecto (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)."""
from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, env

TYPESAFE_API_KEY = env("TYPESAFE_API_KEY")
JEV_MODEL = env("JEV_MODEL", "jev-latest")

# jev_llm: Jev decide y el LLM redacta | jev: Jev decide, textos fijos | llm: el LLM hace todo
# "libre" es el agente LLM original del proyecto (app/inference/pipeline.py), para comparar.
MODES = {"jev_llm": "Jev + LLM", "jev": "Solo Jev", "llm": "Solo LLM", "libre": "Agente libre (LLM)"}
MOTOR_MODES = ("jev_llm", "jev", "llm")
DEFAULT_MODE = env("ENGINE_MODE", "jev_llm" if TYPESAFE_API_KEY else "llm")

CONFIDENCE_THRESHOLD = float(env("CONFIDENCE_THRESHOLD", "0.55") or "0.55")
HUMAN_THRESHOLD = float(env("HUMAN_THRESHOLD", "0.7") or "0.7")
# cambiar una respuesta ya dada exige más certeza que llenarla por primera vez
OVERRIDE_THRESHOLD = float(env("OVERRIDE_THRESHOLD", "0.8") or "0.8")

__all__ = ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "TYPESAFE_API_KEY", "JEV_MODEL", "MODES",
           "DEFAULT_MODE", "MOTOR_MODES", "CONFIDENCE_THRESHOLD", "HUMAN_THRESHOLD", "OVERRIDE_THRESHOLD"]

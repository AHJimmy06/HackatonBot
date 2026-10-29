import os
from pathlib import Path

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL", "openai/gpt-oss-120b")
CATALOG_URL = env("CATALOG_URL", "https://ctt-talleresfisei.uta.edu.ec/course.php")
SCRAPE_EVERY_HOURS = float(env("SCRAPE_EVERY_HOURS", "6") or "6")
DB_PATH = ROOT / "data" / "ctt.db"
STATIC_DIR = ROOT / "static"
# Local dashboard access is independent of the bot's original environment contract.
PASSWORD_FILE = ROOT / ".dashboard-password"
DASHBOARD_PASSWORD = PASSWORD_FILE.read_text(encoding="utf-8").strip() if PASSWORD_FILE.exists() else ""

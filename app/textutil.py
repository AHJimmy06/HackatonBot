import re
import unicodedata


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.lower()


def clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").replace("\xa0", " ")).strip()


def clean_block(text: str) -> str:
    lines = [clean(line) for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)

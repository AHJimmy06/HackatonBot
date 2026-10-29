import asyncio
import logging
import re
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.config import CATALOG_URL
from app.persistence.database import replace_courses, set_meta
from app.textutil import clean, clean_block, fold

log = logging.getLogger(__name__)

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:de\s+|-)?([a-z]+)\s*(?:de\s+|-)?(\d{4})?\s+al\s+"
    r"(\d{1,2})\s*(?:de\s+|-)?([a-z]+)\s*(?:de\s+|-)?(\d{4})?",
    re.I,
)
AMOUNT_RE = re.compile(r"(?:\$|usd\s*)\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*usd", re.I)
HOURS_RE = re.compile(r"(\d+)\s*horas", re.I)
HEADERS = {"User-Agent": "CTT-FISEI-Asesor/1.0 (hackathon academico UTA)"}


def parse_range(text: str) -> tuple[str | None, str | None]:
    match = RANGE_RE.search(fold(text))
    if not match:
        return None, None
    d1, m1, y1, d2, m2, y2 = match.groups()
    month1 = MONTHS.get(m1)
    month2 = MONTHS.get(m2)
    if not month1 or not month2:
        return None, None
    year2 = int(y2) if y2 else None
    year1 = int(y1) if y1 else year2
    if not year1 or not year2:
        return None, None
    try:
        start = date(year1, month1, int(d1))
        end = date(year2, month2, int(d2))
    except ValueError:
        return None, None
    return start.isoformat(), end.isoformat()


def compute_status(
    enrollment_start: str | None,
    enrollment_end: str | None,
    course_start: str | None,
    course_end: str | None,
    today: date | None = None,
) -> str:
    today = today or date.today()

    def parse(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    start_enroll, end_enroll, start_course, end_course = map(
        parse, (enrollment_start, enrollment_end, course_start, course_end)
    )
    if start_enroll and end_enroll and start_enroll <= today <= end_enroll:
        return "inscripcion_abierta"
    if start_enroll and today < start_enroll:
        return "por_abrir"
    if end_course and today > end_course:
        return "finalizado"
    if start_course and end_course and start_course <= today <= end_course:
        return "en_curso"
    if end_enroll and today > end_enroll and start_course and today < start_course:
        return "inscripcion_cerrada"
    if end_enroll and today > end_enroll:
        return "finalizado"
    return "sin_fecha"


def _amount(text: str) -> float | None:
    match = AMOUNT_RE.search(fold(text))
    if not match:
        return None
    raw = (match.group(1) or match.group(2)).replace(" ", "")
    if "," in raw and "." not in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _prices(lines: list[str]) -> dict:
    prices: dict[str, float] = {}
    for line in lines:
        value = _amount(line)
        if value is None:
            continue
        folded = fold(line)
        if "estudiante" in folded:
            prices["estudiante_uta"] = value
        elif "publico" in folded:
            prices["publico"] = value
        elif "graduado" in folded or "docente" in folded or "administrativ" in folded:
            prices["uta"] = value
    return prices


def _hours(*chunks: str) -> int | None:
    for chunk in chunks:
        match = HOURS_RE.search(chunk or "")
        if match:
            return int(match.group(1))
    return None


def _listing_cards(soup: BeautifulSoup, page_url: str) -> list[dict]:
    found: dict[str, dict] = {}
    for link in soup.select("a.h5[href]"):
        href = (link.get("href") or "").split("#")[0].strip()
        if not re.fullmatch(r"curso\d+\.php", href):
            continue
        slug = href.replace(".php", "")
        blob = ""
        node = link
        for _ in range(6):
            node = getattr(node, "parent", None)
            if node is None:
                break
            blob = clean(node.get_text(" ", strip=True))
            folded = fold(blob)
            if "horas" in folded and ("abierto" in folded or "cerrado" in folded):
                break
        folded = fold(blob)
        listing = "ABIERTO" if "abierto" in folded else "CERRADO" if "cerrado" in folded else ""
        found[slug] = {
            "slug": slug,
            "url": urljoin(page_url, href),
            "title": clean(link.get_text(" ", strip=True)),
            "hours": _hours(blob),
            "listing_status": listing,
        }
    return list(found.values())


def _field_value(label: str, text: str) -> str:
    raw_label = clean(label).rstrip(":")
    raw_text = clean(text)
    if raw_label and fold(raw_text).startswith(fold(raw_label)):
        return clean(raw_text[len(raw_label):].lstrip(": "))
    return raw_text


def _detail(html: str, base: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.h1.get_text(" ", strip=True)) if soup.h1 else base["title"]
    description = ""
    box = soup.select_one(".descripcion")
    if box:
        description = clean_block(box.get_text("\n", strip=True))
        description = re.sub(r"(?i)^descripcion\s*", "", description).strip()
    objectives = "\n".join(
        clean(item.get_text(" ", strip=True))
        for item in (soup.select(".objetivos li") if soup.select_one(".objetivos") else [])
    )
    place = ""
    location = soup.select_one(".ubicacion")
    if location:
        place = clean_block(location.get_text("\n", strip=True))
        place = re.sub(r"(?i)^lugar de celebracion\s*", "", place).strip()
    instructors = ""
    committee = soup.select_one(".comite")
    if committee:
        instructors = clean_block(committee.get_text("\n", strip=True))

    tab1 = soup.select_one(".pc-tab section .tab1") or soup.select_one("section .tab1")
    tab3 = soup.select_one(".pc-tab section .tab3") or soup.select_one("section .tab3")
    fields: dict[str, str] = {}
    price_lines: list[str] = []
    if tab1:
        for item in tab1.select("li"):
            text = clean(item.get_text(" ", strip=True))
            if not text:
                continue
            price_lines.append(text)
            strong = item.find("strong")
            if not strong:
                continue
            label = fold(clean(strong.get_text(" ", strip=True))).rstrip(":")
            value = _field_value(strong.get_text(" ", strip=True), text)
            fields[label] = value
    note = ""
    if tab1:
        small = tab1.find("small")
        if small:
            note = clean(small.get_text(" ", strip=True))

    def pick(*names: str) -> str:
        for name in names:
            for label, value in fields.items():
                if label.startswith(name):
                    return value
        return ""

    enrollment = pick("fechas de inscripcion", "fecha de inscripcion")
    course_dates = pick("fecha del curso", "fechas del curso")
    enrollment_start, enrollment_end = parse_range(enrollment)
    course_start, course_end = parse_range(course_dates)
    contents = clean_block(tab3.get_text("\n", strip=True)) if tab3 else ""
    contents = re.sub(r"(?i)^contenidos\s*", "", contents).strip()
    course = {
        **base,
        "title": title or base["title"],
        "description": description[:3000],
        "objectives": objectives[:2000],
        "place": place[:1000],
        "instructors": instructors[:1000],
        "enrollment_start": enrollment_start,
        "enrollment_end": enrollment_end,
        "course_start": course_start,
        "course_end": course_end,
        "schedule": pick("horario")[:500],
        "modality": pick("modalidad")[:200],
        "capacity": pick("cupo")[:200],
        "prerequisites": pick("conocimientos previos")[:500],
        "certificate": pick("tipo de capacitacion")[:300],
        "prices": _prices(price_lines),
        "contents": contents[:4000],
        "note": note[:400],
        "hours": _hours(pick("horas de capacitacion"), base.get("title") or "") or base.get("hours"),
    }
    course["computed_status"] = compute_status(
        course["enrollment_start"],
        course["enrollment_end"],
        course["course_start"],
        course["course_end"],
    )
    return course


async def _get(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url, headers=HEADERS)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


async def refresh() -> int:
    set_meta("catalog_refreshing", "1")
    set_meta("catalog_error", "")
    try:
        timeout = httpx.Timeout(30.0, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            listing_html = await _get(client, CATALOG_URL)
            cards = _listing_cards(BeautifulSoup(listing_html, "html.parser"), CATALOG_URL)
            if not cards:
                raise RuntimeError("El listado no devolvió cursos.")
            semaphore = asyncio.Semaphore(4)

            async def one(card: dict) -> dict:
                async with semaphore:
                    try:
                        html = await _get(client, card["url"])
                        return _detail(html, card)
                    except Exception:
                        log.exception("No se pudo leer %s", card["url"])
                        card = dict(card)
                        card["computed_status"] = "sin_fecha"
                        card["description"] = ""
                        return card

            courses = await asyncio.gather(*(one(card) for card in cards))
        count = replace_courses(courses)
        set_meta("catalog_updated_at", datetime.now().isoformat(timespec="seconds"))
        set_meta("catalog_count", str(count))
        return count
    except Exception as exc:
        set_meta("catalog_error", str(exc))
        raise
    finally:
        set_meta("catalog_refreshing", "0")


if __name__ == "__main__":
    from app.persistence.database import init_db, list_courses

    logging.basicConfig(level=logging.INFO)
    init_db()
    total = asyncio.run(refresh())
    print(f"{total} cursos")
    for course in list_courses():
        prices = course["prices"]
        print(
            f"{course['slug']:8} {course['computed_status']:22} "
            f"{course['hours'] or '-':>3}h  {prices}  {course['title']}"
        )

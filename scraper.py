#!/usr/bin/env python3
"""Lunch Hub scraper v2.2.

Parses each restaurant separately and only accepts prices with an explicit
currency marker (Kc/CZK or ,-). If a page layout changes, the restaurant is
marked as unavailable rather than publishing guessed prices.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Prague")
OUT = Path("data/menu.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 LunchHub/2.2"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.7",
}

SOURCES = [
    {
        "name": "V Domově",
        "url": "https://restauracevdomove.cz/jidelni-listek-2/",
        "urls": ["https://restauracevdomove.cz/jidelni-listek-2/"],
        "parser": "domove",
    },
    {
        "name": "Pod Párou",
        "url": "https://podparou.eu/denni-menu/",
        "urls": ["https://podparou.eu/denni-menu/"],
        "parser": "podparou",
    },
    {
        "name": "Hamburg",
        "url": "http://www.restauracehamburg.cz/",
        "urls": [
            "https://www.restauracehamburg.cz/",
            "https://restauracehamburg.cz/",
            "http://www.restauracehamburg.cz/",
            "http://restauracehamburg.cz/",
        ],
        "parser": "hamburg",
    },
    {
        "name": "U Holiše",
        "url": "https://www.restauraceuholise.cz/denni-menu",
        "urls": ["https://www.restauraceuholise.cz/denni-menu"],
        "parser": "holise",
    },
]

# Crucial: the currency marker is mandatory. Allergen numbers (1,3,7,12),
# portions (180g) and years (2026) are never interpreted as prices.
PRICE_RE = re.compile(r"(?<!\d)(?P<amount>\d{1,4})\s*(?P<currency>,-\s*(?:Kč)?|Kč|CZK)", re.I)
ALLERGEN_CODE = r"(?:1[0-4]|[1-9])[a-z]?"
ALLERGEN_LIST_RE = re.compile(rf"(?:{ALLERGEN_CODE}(?:\s*,\s*{ALLERGEN_CODE})+)", re.I)
DATE_RE = re.compile(
    r"(?:pondělí|úterý|středa|čtvrtek|pátek|sobota|neděle)?\s*\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?",
    re.I,
)

SECTION_HEADINGS = {
    "polévka": "Polévka",
    "menu s polévkou": "Menu s polévkou",
    "denní menu": "Denní menu",
    "denní nabídka": "Denní nabídka",
    "týdenní nabídka": "Týdenní nabídka",
    "zvýhodněné menu jedna": "Zvýhodněné menu 1",
    "zvýhodněné menu dva": "Zvýhodněné menu 2",
    "hlavní chody": "Hlavní jídla",
    "hlavní jídla": "Hlavní jídla",
    "z našeho grilu": "Z našeho grilu",
    "stálá nabídka": "Stálá nabídka",
    "dezert": "Dezerty",
    "dezerty": "Dezerty",
    "nápoj": "Nápoje",
    "nápoje": "Nápoje",
}


def clean(value: str) -> str:
    value = value.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip(" \t\r\n·|")


def fetch(urls: Iterable[str], *, attempts_per_url: int = 2) -> tuple[BeautifulSoup, str]:
    """Fetch a page, retrying transient network/empty-response failures."""
    errors: list[str] = []
    session = requests.Session()
    session.headers.update(HEADERS)
    for url in urls:
        for attempt in range(1, attempts_per_url + 1):
            try:
                response = session.get(
                    url,
                    timeout=(15, 35),
                    allow_redirects=True,
                    headers={
                        "Referer": url.rsplit("/", 1)[0] + "/",
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                    },
                )
                response.raise_for_status()
                body = response.text or ""
                # A short 200 response is often a transient placeholder or a
                # blocked/incomplete page, particularly on Pod Párou.
                if len(body.strip()) < 300:
                    raise ValueError(f"Web vrátil prázdnou nebo neúplnou stránku ({len(body)} znaků)")
                response.encoding = response.apparent_encoding or response.encoding
                return BeautifulSoup(response.text, "html.parser"), response.url
            except Exception as exc:
                errors.append(f"{url} (pokus {attempt}/{attempts_per_url}): {type(exc).__name__}: {exc}")
                if attempt < attempts_per_url:
                    # Back off gently rather than hammering a restaurant site.
                    import time
                    time.sleep(1.5 * attempt)
    raise RuntimeError("; ".join(errors)[-1200:])


def text_lines(soup: BeautifulSoup) -> list[str]:
    # Remove non-content elements but retain line breaks between separate menu
    # labels, dish names and prices. The parser below can pair split name/price
    # lines (used by U Holiše and some WordPress layouts).
    for node in soup(["script", "style", "noscript", "svg", "iframe"]):
        node.decompose()
    raw = soup.get_text("\n", strip=True)
    return [clean(line) for line in raw.splitlines() if clean(line)]


def parse_prices(line: str) -> tuple[str, str] | None:
    """Return (text before price, formatted price) for currency-marked prices."""
    matches = list(PRICE_RE.finditer(line))
    if not matches:
        return None

    valid = []
    for match in matches:
        amount = int(match.group("amount"))
        # Menu prices above 1,500 Kč are very unlikely and often indicate a
        # date/number accidentally joined to a currency label.
        if 10 <= amount <= 1500:
            valid.append(match)
    if not valid:
        return None

    first = valid[0]
    last = valid[-1]
    name = line[: first.start()].strip()
    amounts = [f"{int(m.group('amount'))} Kč" for m in valid]
    trailing = line[last.end() :].strip(" /-·")
    price = " / ".join(amounts)
    if trailing and re.search(r"vegetari|veggie|vegetarian", trailing, re.I):
        price += " (vegetariánská varianta)"
    return name, price


def strip_allergens(name: str) -> str:
    name = clean(name)
    # Allergen lists can appear before or after the dish name, e.g. Hamburg:
    # "1,3,7 Dršťková" or Pod Párou: "Gulášová 1 45,-".
    name = re.sub(rf"^\s*{ALLERGEN_CODE}(?:\s*,\s*{ALLERGEN_CODE})+\s*", "", name, flags=re.I)
    name = re.sub(rf"\s*\(\s*{ALLERGEN_CODE}(?:\s*,\s*{ALLERGEN_CODE})+\s*\)\s*", " ", name, flags=re.I)
    # Remove comma-separated allergens immediately before an optional portion
    # (e.g. "... 1,7,10 180g").
    name = re.sub(
        rf"\s+{ALLERGEN_CODE}(?:\s*,\s*{ALLERGEN_CODE})+(?:\s*)(?=(?:\d+(?:[.,]\d+)?\s*(?:g|ml|l)\b)?$)",
        " ",
        name,
        flags=re.I,
    )
    # A single trailing allergen number is common on Pod Párou ("Gulášová 1").
    name = re.sub(rf"\s+{ALLERGEN_CODE}\s*$", "", name, flags=re.I)
    return clean(name)


def is_date_or_heading_noise(line: str) -> bool:
    low = line.casefold()
    if not line:
        return True
    if low in {"menu", "denní menu", "denní nabídka", "jídelní lístek"}:
        return True
    if low.startswith((
        "aktuální nabídka na dnešní den",
        "kompletní jídelní lístek najdete",
        "v restauraci pod párou pro vás připravujeme",
        "denní nabídku podáváme",
        "pokud se vám nezobrazuje",
        "informace o alergenech",
        "denní menu se podává",
        "změna jídel",
        "změna jídelníčku",
        "menu v nabídce do vyprodání",
        "využijte nabídky denního menu",
        "restaurace ",
        "vítejte na webové stránce",
        "jankovcova ",
        "u uranie ",
        "po–",
        "po -",
        "pondělí –",
        "pondělí -",
    )):
        return True
    if low.startswith("image") or re.match(r"^\d+_\d+", line):
        return True
    if re.match(r"^\d{1,2}[a-z]?\s+(?:obiloviny|mléko|vejce|celer|hořčice|oxid|ryby|korýši|sezam|sója|arašídy)", low):
        return True
    if re.fullmatch(r"\d{1,2}[a-z]?", line, re.I):
        return True
    if DATE_RE.search(line) and (len(line) < 45 or re.search(r"denní menu|dnešní menu", low)):
        return True
    if re.search(r"\b(?:alergeny|kontakty|otevřeno|telefon|e-mail|adresa)\b", low) and len(line) < 100:
        return True
    return False


def normalize_item_name(lines: list[str]) -> str:
    # Join name fragments but remove duplicated whitespace/adjacent repeats.
    parts: list[str] = []
    for line in lines:
        value = clean(line)
        if is_date_or_heading_noise(value):
            continue
        if value and (not parts or value.casefold() != parts[-1].casefold()):
            parts.append(value)
    name = strip_allergens(" ".join(parts))
    # Remove stray leading bullets/dashes and trailing punctuation.
    name = re.sub(r"^[•*\-–—]+\s*", "", name)
    return clean(name).strip(" ,;:-")


def parse_rows(
    lines: list[str],
    *,
    start: int,
    stop_patterns: tuple[str, ...] = (),
    default_section: str = "Denní nabídka",
    custom_headings: bool = True,
) -> list[dict]:
    """Pair visible name fragments with explicit-currency price lines."""
    sections: list[dict] = []
    current = {"title": default_section, "items": []}
    pending: list[str] = []

    def ensure_section(title: str) -> None:
        nonlocal current
        # Avoid a run of empty duplicate sections.
        if current["items"] or not sections:
            sections.append(current)
        current = {"title": title, "items": []}

    for raw in lines[start:]:
        line = clean(raw)
        low = line.casefold()
        if any(re.search(pattern, line, re.I) for pattern in stop_patterns):
            break

        heading = SECTION_HEADINGS.get(low) if custom_headings else None
        if heading:
            pending = []
            ensure_section(heading)
            continue

        if is_date_or_heading_noise(line):
            continue

        price_data = parse_prices(line)
        if price_data:
            name_part, price = price_data
            if name_part:
                pending.append(name_part)
            name = normalize_item_name(pending)
            pending = []
            if len(name) < 3:
                continue
            # Keep only meaningful rows. An isolated section label or allergen
            # code must never become an item.
            if name.casefold() in SECTION_HEADINGS:
                continue
            current["items"].append({"name": name, "price": price})
            continue

        # Avoid collecting the page's navigation, footer or allergen legend.
        if len(line) > 260:
            continue
        if low in {"čeština", "english", "úvod", "kontakt", "rezervace", "fotogalerie"}:
            continue
        if re.match(r"^(?:1[0-4]|1[0-4][a-z]?)(?:\s|$)", line) and re.search(
            r"obiloviny|mléko|vejce|celer|hořčice|oxid|ryby|korýši|sezam|sója", low
        ):
            continue
        pending.append(line)

    if current["items"] or not sections:
        sections.append(current)
    return [section for section in sections if section["items"]]


def split_general_sections(items: list[dict]) -> list[dict]:
    soups, mains, desserts = [], [], []
    for item in items:
        title = item["name"].casefold()
        if re.search(r"dršťkov|vývar|gulášov|česnečk|zelňačk|polév|polévka", title):
            soups.append(item)
        elif re.search(r"dort|dezert|cheesecake|koláč|zmrzlin", title):
            desserts.append(item)
        else:
            mains.append(item)
    result = []
    if soups:
        result.append({"title": "Polévky", "items": soups})
    if mains:
        result.append({"title": "Hlavní jídla", "items": mains})
    if desserts:
        result.append({"title": "Dezerty", "items": desserts})
    return result


def find_last(lines: list[str], pattern: str, *, before: int | None = None) -> int:
    limit = len(lines) if before is None else before
    found = [i for i, line in enumerate(lines[:limit]) if re.search(pattern, line, re.I)]
    if not found:
        raise ValueError(f"Na stránce nebyla nalezena sekce: {pattern}")
    return found[-1]


def parse_domove(soup: BeautifulSoup) -> list[dict]:
    lines = text_lines(soup)
    start = find_last(lines, r"^Denní nabídka$")

    # The page contains the current offer first and the permanent offer below
    # it. Keep both blocks rather than stopping at the "Stálá nabídka" heading.
    result: list[dict] = []
    current_title = "Polévka"
    current_items: list[dict] = []
    pending: list[str] = []
    headings = {
        "menu s polévkou": "Menu s polévkou",
        "denní menu": "Denní menu",
        "stálá nabídka": "Stálá nabídka",
    }

    for line in lines[start + 1:]:
        low = line.casefold()
        if low.startswith((
            "kompletní nabídku najdete",
            "alergeny na vyžádání",
            "změna jídel vyhrazena",
        )):
            break

        if low in headings:
            if current_items:
                result.append({"title": current_title, "items": current_items})
            current_title = headings[low]
            current_items = []
            pending = []
            continue

        if is_date_or_heading_noise(line):
            continue

        price_data = parse_prices(line)
        if price_data:
            name_part, price = price_data
            if name_part:
                pending.append(name_part)
            name = normalize_item_name(pending)
            pending = []
            if len(name) >= 3:
                current_items.append({"name": name, "price": price})
        else:
            if len(line) < 260:
                pending.append(line)

    if current_items:
        result.append({"title": current_title, "items": current_items})
    if not result:
        raise ValueError("Denní a stálou nabídku se nepodařilo rozparsovat (nebyla nalezena jídla s cenou)")
    return result


def parse_podparou(soup: BeautifulSoup) -> list[dict]:
    lines = text_lines(soup)
    # The site has duplicate navigation headings. Use the last content heading
    # that is followed by a weekday/date line.
    candidates = [i for i, line in enumerate(lines) if re.fullmatch(r"Denní menu", line, re.I)]
    start = None
    for idx in reversed(candidates):
        nearby = " ".join(lines[idx + 1 : idx + 7])
        if DATE_RE.search(nearby):
            start = idx
            break
    if start is None:
        start = find_last(lines, r"^Denní menu$")

    sections = parse_rows(
        lines,
        start=start + 1,
        stop_patterns=(r"^Informace o alergenech", r"^Vítejte na webové stránce", r"^Denní menu se podává"),
        default_section="Denní nabídka",
        custom_headings=False,
    )
    items = [item for section in sections for item in section["items"]]
    if not items:
        raise ValueError("Na stránce Pod Párou nebyla nalezena žádná položka s cenou")
    return split_general_sections(items)


def parse_hamburg(soup: BeautifulSoup) -> list[dict]:
    lines = text_lines(soup)
    start = find_last(lines, r"Dnešní menu")

    # The homepage has two consecutive blocks: today's menu and the weekly
    # offer. Keep the weekly block under its own heading instead of stopping
    # when the heading is encountered.
    parsed = parse_rows(
        lines,
        start=start + 1,
        stop_patterns=(r"^Kontakt$", r"^Otevírací doba$", r"^Otevřeno$"),
        default_section="Denní nabídka",
        custom_headings=True,
    )

    daily_items: list[dict] = []
    weekly_items: list[dict] = []
    for section in parsed:
        if section["title"].casefold() == "týdenní nabídka":
            weekly_items.extend(section["items"])
        else:
            daily_items.extend(section["items"])

    # The homepage continues with a drinks list below the weekly food offer.
    # Keep the weekly section focused on meals; the drinks block is not part
    # of the weekly lunch menu. One malformed visual separator can also be
    # combined with the first beverage row, so filter both by content.
    weekly_drink_re = re.compile(
        r"\b(?:radler|limon[aá]d\w*|kofola|fresh\s+čaj|čaj|cider|"
        r"nealko\w*|nealkohol\w*|pivo|káva|espresso|cola|voda|džus|juice|nápoj\w*)\b",
        re.I,
    )
    cleaned_weekly: list[dict] = []
    for item in weekly_items:
        item_name = clean(item.get("name", ""))
        # Discard separator fragments and beverage items accidentally included
        # in the homepage's weekly-offer text block.
        if re.search(r"[+]{3,}", item_name) or weekly_drink_re.search(item_name):
            continue
        if len(re.sub(r"[^A-Za-zÁ-ž]", "", item_name)) < 5:
            continue
        cleaned_weekly.append(item)
    weekly_items = cleaned_weekly

    if not daily_items:
        raise ValueError("Na hlavní stránce Hamburg nebyly nalezeny řádky dnešního menu s cenou")

    result = split_general_sections(daily_items)
    if weekly_items:
        result.append({"title": "Týdenní nabídka", "items": weekly_items})
    else:
        # Keep the current daily menu visible, but surface an explicit warning
        # in the Action log if the weekly heading exists without priced items.
        if any(line.casefold() == "týdenní nabídka" for line in lines[start + 1:]):
            print("WARNING | Hamburg: nadpis Týdenní nabídka nalezen, ale nepodařilo se vytáhnout položky s cenou")
    return result


def parse_holise(soup: BeautifulSoup) -> list[dict]:
    lines = text_lines(soup)
    start = find_last(lines, r"^Denní menu\s*[-–]")
    sections = parse_rows(
        lines,
        start=start + 1,
        stop_patterns=(r"^Alergeny$", r"^KONTAKTY$", r"^Změna jídelníčku vyhrazena"),
        default_section="Denní menu",
        custom_headings=True,
    )
    if not sections:
        raise ValueError("U Holiše nebyly nalezeny položky denního menu s cenou")
    return sections


PARSERS = {
    "domove": parse_domove,
    "podparou": parse_podparou,
    "hamburg": parse_hamburg,
    "holise": parse_holise,
}


def main() -> None:
    now = datetime.now(TZ)
    result = {
        "updated_at": now.isoformat(),
        "updated_label": now.strftime("%d.%m.%Y %H:%M"),
        "date": now.strftime("%d.%m.%Y"),
        "restaurants": [],
    }

    success_count = 0
    for source in SOURCES:
        record = {
            "name": source["name"],
            "url": source["url"],
            "sections": [],
            "ok": False,
            "status": "Menu se nepodařilo načíst",
            "error": "",
        }
        try:
            parse_attempts = 3 if source["parser"] == "podparou" else 1
            last_error: Exception | None = None
            for attempt in range(1, parse_attempts + 1):
                try:
                    soup, final_url = fetch(
                        source["urls"],
                        attempts_per_url=1 if source["parser"] == "podparou" else 2,
                    )
                    sections = PARSERS[source["parser"]](soup)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < parse_attempts:
                        import time
                        print(f"RETRY | {source['name']}: pokus {attempt}/{parse_attempts} nevyšel: {exc}")
                        time.sleep(2 * attempt)
            if last_error is not None:
                raise last_error
            item_count = sum(len(section["items"]) for section in sections)
            if item_count == 0:
                raise ValueError("Parser nevrátil žádné položky")
            record.update(
                {
                    "sections": sections,
                    "ok": True,
                    "status": f"Menu načteno ({item_count} položek)",
                    "url": final_url,
                    "error": "",
                }
            )
            success_count += 1
            print(f"OK | {source['name']}: {item_count} položek")
            for section in sections:
                print(f"  {section['title']}: {len(section['items'])}")
                for item in section["items"]:
                    print(f"    - {item['name']} | {item['price']}")
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]
            record["status"] = "Načtení menu se nezdařilo"
            print(f"ERROR | {source['name']}: {record['error']}")

        result["restaurants"].append(record)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nHotovo: {success_count}/{len(SOURCES)} restaurací načteno. JSON: {OUT}")
    # Fail the workflow only if every source failed. A partial result is still
    # useful and is published with clear per-restaurant error messages.
    if success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

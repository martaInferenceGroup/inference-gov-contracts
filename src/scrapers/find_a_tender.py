"""Fetch notices from Find a Tender via its web search form.

Find a Tender has no keyword search API — only an OCDS bulk data endpoint.
This module uses the website's POST-based search (same as the advanced search
page) with a proper session and CSRF token.
"""

import hashlib
import html as html_mod
import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SEARCH_URL = "https://www.find-tender.service.gov.uk/Search/Results"

STAGE_MAP = {
    "tender": "stage[4]",
    "planning": "stage[1]",
    "award": "stage[5]",
    "contract": "stage[3]",
}


def _session() -> requests.Session:
    """Create a session with retry logic."""
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"
    )
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _parse_date(s: str) -> str:
    """Convert '31 March 2026' to '2026-03-31'. Returns original if parsing fails."""
    for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    return s


def fetch_notices(keywords: list[str], max_pages: int = 5,
                  min_value: float | None = None,
                  max_value: float | None = None,
                  stages: list[str] | None = None,
                  published_from: datetime | None = None,
                  published_to: datetime | None = None,
                  **_kwargs) -> list[dict]:
    """Fetch notices from Find a Tender matching keywords.

    If keyword list is very long, batches into groups to avoid form errors.
    """
    # FaT form can't handle very long keyword queries — batch if needed
    MAX_KEYWORDS_PER_QUERY = 15
    if len(keywords) > MAX_KEYWORDS_PER_QUERY:
        all_results = []
        seen_ids: set[str] = set()
        for i in range(0, len(keywords), MAX_KEYWORDS_PER_QUERY):
            batch = keywords[i:i + MAX_KEYWORDS_PER_QUERY]
            try:
                batch_results = fetch_notices(
                    keywords=batch, max_pages=max_pages,
                    min_value=min_value, max_value=max_value,
                    stages=stages, published_from=published_from,
                    published_to=published_to,
                )
                for r in batch_results:
                    if r["ocid"] not in seen_ids:
                        seen_ids.add(r["ocid"])
                        all_results.append(r)
            except Exception as e:
                print(f"  FaT batch {i//MAX_KEYWORDS_PER_QUERY + 1} error: {e}")
        return all_results

    query = " OR ".join(f'"{kw}"' for kw in keywords)

    session = _session()

    # Step 1: GET to establish session cookie and get form defaults + CSRF token
    r = session.get(SEARCH_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    form = soup.find("form", {"id": "search_form"})
    if not form:
        raise RuntimeError("Could not find search form on Find a Tender")

    # Collect all default form values
    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name", "")
        if not name:
            continue
        itype = inp.get("type", "text")
        if itype == "hidden":
            data[name] = inp.get("value", "")
        elif itype == "checkbox":
            # Check both aria-checked and the checked attribute
            if inp.get("aria-checked") == "true" or inp.has_attr("checked"):
                data[name] = inp.get("value", "1")
        elif itype == "text":
            data[name] = inp.get("value", "")

    # Set search parameters
    data["keywords"] = query
    data["adv_search"] = ""  # submit button

    # Stage filters
    if stages:
        for key in list(data.keys()):
            if key.startswith("stage["):
                del data[key]
        for s in stages:
            skey = STAGE_MAP.get(s)
            if skey:
                data[skey] = "1"

    # Value filters
    if min_value is not None:
        data["minimum_value"] = str(int(min_value))
    if max_value is not None:
        data["maximum_value"] = str(int(max_value))

    # Date filters
    if published_from:
        data["published_from[day]"] = str(published_from.day)
        data["published_from[month]"] = str(published_from.month)
        data["published_from[year]"] = str(published_from.year)
    if published_to:
        data["published_to[day]"] = str(published_to.day)
        data["published_to[month]"] = str(published_to.month)
        data["published_to[year]"] = str(published_to.year)

    # Step 2: POST search, paginate
    all_results = []
    page = 1

    while page <= max_pages:
        if page > 1:
            data["page"] = str(page)

        r = session.post(SEARCH_URL, data=data, timeout=30)
        r.raise_for_status()

        if "syserror" in r.url:
            raise RuntimeError(f"Find a Tender returned an error page: {r.url}")

        soup = BeautifulSoup(r.text, "html.parser")
        results = _parse_results_page(soup)

        if not results:
            break

        all_results.extend(results)

        next_link = soup.select_one('a[aria-label="Go to next page"]')
        if not next_link:
            break

        page += 1

    return all_results


def _parse_results_page(soup: BeautifulSoup) -> list[dict]:
    """Parse search result entries from an HTML results page."""
    results = []

    for header in soup.select(".search-result-header"):
        entry_div = header.find_parent("div", class_="search-result")
        if not entry_div:
            entry_div = header.parent

        title = html_mod.unescape(header.get("title", header.get_text(strip=True)))

        # Buyer name
        buyer_el = entry_div.select_one(".search-result-sub-header") if entry_div else None
        buyer = buyer_el.get_text(strip=True) if buyer_el else ""

        # Description — from the second <div class="wrap-text"> (first is buyer name)
        description = ""
        if entry_div:
            wrap_els = entry_div.select("div.wrap-text")
            for wrap_el in wrap_els:
                # Skip the sub-header (buyer name) which also has wrap-text
                if "search-result-sub-header" in (wrap_el.get("class") or []):
                    continue
                text = wrap_el.get_text(" ", strip=True)
                if text and text != buyer:
                    description = text
                    break

        # Extract structured <dt>/<dd> pairs for metadata
        metadata: dict[str, str] = {}
        if entry_div:
            for dt_el in entry_div.select("dt"):
                label = dt_el.get_text(strip=True).lower()
                dd_el = dt_el.find_next_sibling("dd")
                if dd_el:
                    metadata[label] = dd_el.get_text(strip=True)

        # Notice link
        link_el = header.find("a") if header else None
        if not link_el and entry_div:
            link_el = entry_div.find("a", href=re.compile(r"/Notice/"))
        href = link_el.get("href", "") if link_el else ""
        if href and not href.startswith("http"):
            href = f"https://www.find-tender.service.gov.uk{href}"

        # Notice ID
        notice_id_match = re.search(r"/Notice/(\d+-\d+)", href)
        if notice_id_match:
            notice_id = notice_id_match.group(1)
        else:
            notice_id = "fat-" + hashlib.md5((title + buyer).encode()).hexdigest()[:12]

        # Value — from structured metadata first, then regex fallback
        value = None
        for key in ("total value excluding vat", "total value including vat", "total value"):
            if key in metadata:
                value_match = re.search(r"[\d,]+(?:\.\d+)?", metadata[key])
                if value_match:
                    try:
                        value = float(value_match.group(0).replace(",", ""))
                    except ValueError:
                        pass
                break

        # Dates — from structured metadata
        published = ""
        closing = ""
        for key, val in metadata.items():
            if "publication" in key:
                published = _parse_date(val)
            elif "submission" in key or "closing" in key or "deadline" in key:
                closing = _parse_date(val)

        # Location — from structured metadata
        location = ""
        for key, val in metadata.items():
            if "location" in key:
                location = val
                break

        # Notice type from metadata
        notice_type_str = metadata.get("notice type", "")

        # Type classification
        ct = "Tender"
        notice_lower = notice_type_str.lower()
        if "award" in notice_lower:
            ct = "Contract"
        elif "pipeline" in notice_lower or "planning" in notice_lower or "engagement" in notice_lower:
            ct = "Planning"
        elif "contract detail" in notice_lower:
            ct = "Contract"

        results.append({
            "source": "Find a Tender",
            "ocid": notice_id,
            "reference": notice_id,
            "title": title,
            "description": description,
            "buyer": buyer,
            "published_date": published,
            "closing_date": closing,
            "ct": ct,
            "notice_type": notice_type_str or ct,
            "total_value": value,
            "value_high": None,
            "currency": "GBP",
            "cpv_code": "",
            "cpv_description": "",
            "category": "",
            "location": location,
            "link": href,
        })

    return results

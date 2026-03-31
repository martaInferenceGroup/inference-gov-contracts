"""Fetch notices from the Contracts Finder V2 REST API.

Uses POST /api/rest/2/search_notices/json which matches the website's
advanced search — supports keyword OR queries, value ranges, CPV codes,
regions, status filters, and date ranges.
"""

import html as html_mod
import requests
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://www.contractsfinder.service.gov.uk/api/rest/2/search_notices/json"

# Stage names to CF status values
STAGE_TO_STATUS = {
    "tender": "Open",
    "planning": "Pipeline",
    "award": "Awarded",
}


def _session() -> requests.Session:
    """Create a session with retry logic for transient errors."""
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def build_or_query(keywords: list[str]) -> str:
    """Combine keywords into a single OR query string."""
    return " OR ".join(f'"{kw}"' for kw in keywords)


def fetch_notices(keywords: list[str], published_from: datetime,
                  published_to: datetime | None = None,
                  min_value: float | None = None, max_value: float | None = None,
                  location: str | None = None, statuses: list[str] | None = None,
                  cpv_codes: list[str] | None = None,
                  max_results: int = 1000) -> tuple[list[dict], int]:
    """Fetch notices using the V2 search API.

    Returns (results_list, total_hit_count) so callers can warn about truncation.
    """
    criteria: dict = {
        "keyword": build_or_query(keywords),
        "publishedFrom": published_from.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if published_to:
        criteria["publishedTo"] = published_to.strftime("%Y-%m-%dT%H:%M:%S")
    if min_value is not None:
        criteria["valueFrom"] = min_value
    if max_value is not None:
        criteria["valueTo"] = max_value
    if location:
        criteria["regions"] = location
    if statuses:
        criteria["statuses"] = statuses
    if cpv_codes:
        criteria["cpvCodes"] = cpv_codes

    payload = {
        "searchCriteria": criteria,
        "size": min(max_results, 1000),
    }

    session = _session()
    resp = session.post(API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    hit_count = data.get("hitCount", 0)

    results = []
    for entry in data.get("noticeList", []):
        item = entry.get("item", {})
        results.append(_normalise(item, "Contracts Finder"))

    return results, hit_count


def _normalise(item: dict, source: str) -> dict:
    """Convert a V2 API notice item into a flat dict for the dashboard."""
    notice_type = item.get("noticeType", "")
    status = item.get("noticeStatus", "")

    ct_map = {"Contract": "Contract", "Pipeline": "Planning", "PreProcurement": "Planning"}
    ct = ct_map.get(notice_type, "Tender")

    notice_id = item.get("id", "")
    link = f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}" if notice_id else ""

    # Value — prefer valueLow, fall back to valueHigh; show 0 only if both are 0
    value_low = item.get("valueLow")
    value_high = item.get("valueHigh")
    if value_low is not None and value_low != 0:
        value = value_low
    elif value_high is not None and value_high != 0:
        value = value_high
    elif value_low == 0 and value_high == 0:
        value = 0
    else:
        value = None

    # Sanitise text fields
    title = html_mod.unescape(item.get("title", ""))
    description = html_mod.unescape(item.get("description", ""))

    return {
        "source": source,
        "ocid": notice_id,
        "reference": item.get("noticeIdentifier", notice_id),
        "title": title,
        "description": description,
        "buyer": item.get("organisationName", ""),
        "published_date": (item.get("publishedDate") or "")[:10],
        "closing_date": (item.get("deadlineDate") or "")[:10],
        "ct": ct,
        "notice_type": f"{notice_type} - {status}" if status else notice_type,
        "total_value": value,
        "value_high": item.get("valueHigh"),
        "currency": "GBP",
        "cpv_code": item.get("cpvCodes", ""),
        "cpv_description": item.get("cpvDescription", ""),
        "category": item.get("sector", ""),
        "location": item.get("regionText", item.get("region", "")),
        "link": link,
    }

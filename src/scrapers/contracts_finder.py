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


def fetch_full_notice(notice_id: str) -> dict | None:
    """Fetch the complete notice detail for a single contract.

    Returns a rich dict with all available fields including:
    - Full description, award criteria, contact details
    - Attached document links
    - Procurement timeline and procedure type
    - All raw fields from the API

    Returns None if the notice is not found.
    """
    session = _session()

    # The V2 API lets us search by notice ID to get full detail
    payload = {
        "searchCriteria": {"keyword": f'"{notice_id}"'},
        "size": 5,
    }
    resp = session.post(API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Find the exact notice in results
    for entry in data.get("noticeList", []):
        item = entry.get("item", {})
        if item.get("id") == notice_id:
            return _extract_full_notice(item)

    # Fallback: also try the notice page HTML for extra detail
    try:
        page_url = f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}"
        page_resp = session.get(page_url, timeout=30)
        if page_resp.status_code == 200:
            return _extract_from_page(page_resp.text, notice_id)
    except Exception:
        pass

    return None


def _extract_full_notice(item: dict) -> dict:
    """Extract all useful fields from a V2 API notice item."""
    base = _normalise(item, "Contracts Finder")

    # Additional fields not in the normalised output
    base["full_description"] = html_mod.unescape(item.get("description", ""))
    base["contact_name"] = item.get("contactName", "")
    base["contact_email"] = item.get("contactEmail", "")
    base["contact_phone"] = item.get("contactPhone", "")
    base["procedure_type"] = item.get("procedureType", "")
    base["award_criteria"] = item.get("awardCriteria", "")
    base["award_criteria_detail"] = item.get("awardCriteriaDetails", "")
    base["suitable_for_sme"] = item.get("suitableForSme", None)
    base["suitable_for_vcse"] = item.get("suitableForVcse", None)
    base["start_date"] = (item.get("startDate") or "")[:10]
    base["end_date"] = (item.get("endDate") or "")[:10]
    base["duration_months"] = item.get("durationMonths")

    # Document attachments
    documents = []
    for doc in item.get("documents", []):
        documents.append({
            "name": doc.get("fileName", ""),
            "description": doc.get("description", ""),
            "url": doc.get("url", ""),
        })
    for doc in item.get("attachments", []):
        documents.append({
            "name": doc.get("fileName", doc.get("name", "")),
            "description": doc.get("description", ""),
            "url": doc.get("url", doc.get("href", "")),
        })
    base["documents"] = documents

    # Additional info sections
    base["additional_text"] = item.get("additionalText", "")
    base["lot_details"] = item.get("lots", [])

    # Raw item preserved for anything we missed
    base["_raw"] = item

    return base


def _extract_from_page(html_text: str, notice_id: str) -> dict:
    """Fallback: extract notice details from the HTML page."""
    import re

    result = {
        "source": "Contracts Finder",
        "ocid": notice_id,
        "link": f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}",
        "full_description": "",
        "documents": [],
    }

    # Extract text content between common section headers
    # Title
    title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_text, re.DOTALL)
    if title_match:
        result["title"] = html_mod.unescape(re.sub(r'<[^>]+>', '', title_match.group(1)).strip())

    # Description — usually in a div after "Description" header
    desc_match = re.search(
        r'(?:Description|About the contract)</(?:h[23]|dt)>\s*<(?:div|dd)[^>]*>(.*?)</(?:div|dd)>',
        html_text, re.DOTALL | re.IGNORECASE
    )
    if desc_match:
        desc = re.sub(r'<[^>]+>', ' ', desc_match.group(1))
        result["full_description"] = html_mod.unescape(desc).strip()

    # Document links
    for doc_match in re.finditer(
        r'href="([^"]*)"[^>]*>\s*(.*?)\s*</a>',
        html_text, re.DOTALL
    ):
        url, name = doc_match.groups()
        name = re.sub(r'<[^>]+>', '', name).strip()
        if any(ext in url.lower() for ext in ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip')):
            result["documents"].append({"name": name, "url": url, "description": ""})

    return result

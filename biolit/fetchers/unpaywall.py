"""Fetch open-access full-text PDFs via the Unpaywall API."""
import os
import requests


_UNPAYWALL_BASE = "https://api.unpaywall.org/v2/{doi}"


def fetch_via_unpaywall(doi: str, email: str | None = None) -> bytes | None:
    """Download the best open-access PDF for *doi* using Unpaywall.

    *email* is required by Unpaywall's terms of service. Falls back to the
    UNPAYWALL_EMAIL environment variable when not supplied as an argument.

    Returns raw PDF bytes, or None if no OA copy is found or an error occurs.
    """
    if not doi:
        return None
    email = email or os.environ.get("UNPAYWALL_EMAIL")
    if not email:
        raise ValueError(
            "An email address is required for the Unpaywall API. "
            "Pass --unpaywall-email or set UNPAYWALL_EMAIL."
        )
    try:
        meta_resp = requests.get(
            _UNPAYWALL_BASE.format(doi=doi),
            params={"email": email},
            timeout=15,
        )
        meta_resp.raise_for_status()
        data = meta_resp.json()

        best_oa = data.get("best_oa_location") or {}
        pdf_url = best_oa.get("url_for_pdf")
        if not pdf_url:
            return None

        pdf_resp = requests.get(pdf_url, timeout=60)
        pdf_resp.raise_for_status()
        if "pdf" not in pdf_resp.headers.get("Content-Type", "").lower():
            # Some URLs redirect to HTML landing pages — skip them
            return None
        return pdf_resp.content
    except Exception:
        return None


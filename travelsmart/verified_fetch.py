"""HTTPS fetch with full certificate verification, plus a publisher's missing intermediate CA.

miv-opendata.belfla.be (Flemish Traffic Center detector feed) presents a valid `*.belfla.be` certificate but
omits its intermediate CA, so a strict client cannot build the chain from the certifi roots alone. The public
DigiCert intermediate is shipped in config/certs/ and added to the trust store; the chain and the host name are
both verified as usual. Nothing is relaxed.
"""
from __future__ import annotations

import logging
import ssl
from pathlib import Path

import certifi
import httpx

USER_AGENT = "TravelSmart/0.1 (open-data ingest)"


def verified_context(extra_ca: Path | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    if extra_ca:
        context.load_verify_locations(cafile=str(extra_ca))
    return context


def fetch(url: str, *, extra_ca: Path | None = None, timeout: float = 60) -> bytes:
    """GET `url` over a fully verified connection. Redirects are never followed."""
    with httpx.Client(verify=verified_context(extra_ca), timeout=timeout, follow_redirects=False,
                      headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}) as client:
        response = client.get(url)
        response.raise_for_status()
        logging.info("fetched %s: %s bytes on the wire (%s), %s bytes decoded", url, response.num_bytes_downloaded,
                     response.headers.get("content-encoding") or "uncompressed", len(response.content))
        return response.content

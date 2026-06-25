"""Voyage AI embedding helper.

Used both at seed time and for online query embedding. If ``VOYAGE_API_KEY``
isn't set, the helper raises so callers can fall back to keyword search.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import urllib.error
import urllib.request
import json

logger = logging.getLogger(__name__)


def _voyage_embed(texts: list[str], input_type: str) -> list[list[float]]:
    api_key = os.environ.get("VOYAGE_API_KEY", "")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY is not set")

    payload = json.dumps(
        {
            "input": texts,
            "model": os.environ.get("VOYAGE_MODEL", "voyage-3"),
            "input_type": input_type,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.voyageai.com/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Voyage embed failed: {exc.code} {detail}") from exc

    return [item["embedding"] for item in body["data"]]


def embed_documents(texts: list[str]) -> list[list[float]]:
    return _voyage_embed(texts, input_type="document")


def embed_query(text: str) -> list[float]:
    return _voyage_embed([text], input_type="query")[0]

"""
Pattern: AI Model Cascade — Exhausting free-tier quota buckets before failing.

Context: A product sourcing feature that uses a vision model to extract
structured data from product images. Target: Google AI Studio free tier
(1,500 requests/day per model, per API version, per API key).

Strategy:
  Try a list of (api_version, model_name) pairs in order. On a 200, return.
  On a 404 (model doesn't exist in this version), skip silently.
  On a 429 (quota exhausted), move to the next bucket.
  On other errors, log and continue.

What you give up:
  - Latency: falling through N failed models costs N HTTP round-trips.
  - Quota: each cascade step that reaches a model consumes a quota unit.
  - Maintenance: the model list decays as Google deprecates version strings.

This file has no framework dependencies — copy it into any Python project.
"""
import base64
import json
import logging
import re
from io import BytesIO
from typing import Optional

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# Models to try, in priority order.
# Put highest-capacity / most stable models first.
# Update this list when Google announces deprecations.
MODELS_TO_TRY = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.5-pro",
]

API_VERSIONS = ["v1", "v1beta"]

GEMINI_BASE = "https://generativelanguage.googleapis.com"

# Maximum image dimension before sending to the model.
# Keeps token count low and avoids exceeding context window limits.
MAX_IMAGE_DIM = 1024


def resize_for_ai(image_bytes: bytes, max_dim: int = MAX_IMAGE_DIM) -> tuple[bytes, str]:
    """
    Resize image to max_dim on the longest side and re-encode as JPEG.
    Returns (jpeg_bytes, mime_type).
    """
    img = Image.open(BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

    out = BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue(), "image/jpeg"


def analyze_image(
    api_key: str,
    image_url: str,
    prompt: str,
) -> Optional[dict]:
    """
    Send an image to the Gemini API and return parsed JSON from the response.

    Returns None if all models fail or quota is fully exhausted.
    Returns a dict on success.
    """
    # Download and resize the image once, reuse for every cascade attempt.
    try:
        raw = requests.get(image_url, timeout=15)
        raw.raise_for_status()
        img_bytes, mime_type = resize_for_ai(raw.content)
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        logger.info("Image prepared: %d bytes (JPEG, max %dpx)", len(img_bytes), MAX_IMAGE_DIM)
    except Exception as exc:
        logger.error("Image preparation failed: %s", exc)
        return None

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": img_b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1024,
        },
    }

    hit_quota = False

    for version in API_VERSIONS:
        for model in MODELS_TO_TRY:
            url = f"{GEMINI_BASE}/{version}/models/{model}:generateContent?key={api_key}"
            logger.debug("Trying %s / %s", version, model)

            try:
                resp = requests.post(url, json=payload, timeout=30)
            except requests.RequestException as exc:
                logger.warning("Network error on %s/%s: %s", version, model, exc)
                continue

            if resp.status_code == 200:
                return _extract_json(resp.json())

            if resp.status_code == 404:
                logger.debug("Model %s not found in %s — skipping", model, version)
                continue

            if resp.status_code == 429:
                logger.warning("Quota exhausted: %s / %s", version, model)
                hit_quota = True
                continue

            logger.warning("Unexpected status %d on %s/%s", resp.status_code, version, model)

    if hit_quota:
        logger.error("All quota buckets exhausted. Daily limit reached.")
    else:
        logger.error("No working model found. Check model list for deprecations.")

    return None


def _extract_json(response_data: dict) -> Optional[dict]:
    """
    Pull the first text candidate from a Gemini response and parse it as JSON.
    Gemini sometimes wraps JSON in markdown fences — strip those first.
    """
    try:
        text = (
            response_data
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        # Strip markdown code fences if present
        match = re.search(r"(\{.*\})", text.strip(), re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except Exception as exc:
        logger.warning("JSON extraction failed: %s", exc)
    return None

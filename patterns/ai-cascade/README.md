# Pattern: AI Model Cascade

## The problem

Google AI Studio's free tier gives you 1,500 requests/day — but the limit
applies *per model, per API version, per API key*. `gemini-2.0-flash` on
`v1beta` and `gemini-2.5-flash` on `v1` have independent quota buckets.

A single-model implementation hits its limit and returns 429 for the rest
of the day. A cascade exhausts all available buckets before failing.

## Trade-offs at a glance

| | Single model | Cascade |
|---|---|---|
| Daily capacity | 1,500 req | ~7,000–12,000 req (across buckets) |
| Latency on quota hit | Immediate 429 | N × round-trip per failed model |
| Quota consumed per request | 1 unit | 1 unit (success) or N units (cascade) |
| Maintenance | Low | Medium (model list decays) |

## Usage

```python
from ai_cascade import analyze_image

result = analyze_image(
    api_key="your-api-key",
    image_url="https://example.com/product.jpg",
    prompt="Extract product name, category, and price range as JSON.",
)

if result:
    print(result["product_name"])
else:
    print("All models exhausted or failed.")
```

## Dependencies

```
pip install requests Pillow
```

## Keeping the model list current

List currently supported models for your API key:

```python
import requests

def list_models(api_key, version="v1beta"):
    url = f"https://generativelanguage.googleapis.com/{version}/models?key={api_key}"
    r = requests.get(url, timeout=10)
    if r.ok:
        return [m["name"] for m in r.json().get("models", [])]
    return []

print(list_models("your-api-key"))
```

Run this periodically and update `MODELS_TO_TRY` in `ai_cascade.py` when
models are added or deprecated.

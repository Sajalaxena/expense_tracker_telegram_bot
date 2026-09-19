"""Thin Google Gemini REST client (stdlib only, no SDK dependency)."""

import json
import urllib.request

GEMINI_MODEL = "gemini-flash-lite-latest"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)


def call_gemini(
    api_key: str,
    contents: list,
    system_instruction: str | None = None,
    json_mode: bool = False,
    max_output_tokens: int = 800,
    temperature: float = 0.6,
    timeout: int = 30,
) -> str:
    """Send a generateContent request and return the response text.

    Raises urllib.error.HTTPError on API errors and ValueError on empty output.
    """
    generation_config = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
    }
    if json_mode:
        generation_config["responseMimeType"] = "application/json"

    body = {"contents": contents, "generationConfig": generation_config}
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    request = urllib.request.Request(
        f"{GEMINI_URL}?key={api_key}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))

    candidates = result.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts).strip()

    if not text:
        raise ValueError("Gemini returned an empty response")

    return text

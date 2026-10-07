"""Telegram Bot API helper using stdlib http.client."""

import json
import http.client

# Largest file we download for photo/voice logging. Telegram's getFile caps
# bots at 20 MB; Gemini's inline-data request limit is ~20 MB after base64.
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


def telegram_api(method: str, payload: dict, token: str, timeout: int = 10) -> dict | None:
    """Call a Bot API method and return its `result`, or None on any failure.

    Never raises.
    """
    try:
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
        conn.request(
            "POST",
            f"/bot{token}/{method}",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read()
        conn.close()
        if not 200 <= response.status < 300:
            print(f"Telegram {method} failed: HTTP {response.status} {body[:300]!r}")
            return None
        return json.loads(body).get("result")
    except Exception as exc:
        print(f"Telegram {method} error: {exc}")
        return None


def send_telegram_message(chat_id: int, text: str, token: str, reply_markup: dict | None = None) -> bool:
    """Send a text message via Telegram Bot API.

    Uses stdlib http.client.HTTPSConnection to POST to the sendMessage endpoint.
    Truncates text to 4096 characters (Telegram's limit).

    Args:
        chat_id: Telegram chat identifier where the message will be sent.
        text: Message text to send (truncated to 4096 chars if longer).
        token: Telegram bot token for authentication.
        reply_markup: Optional inline keyboard ({"inline_keyboard": [[...]]}).

    Returns:
        True on success, False on any failure. Never raises exceptions.
    """
    try:
        # Truncate to Telegram's 4096 character limit
        if len(text) > 4096:
            text = text[:4096]

        data = {"chat_id": chat_id, "text": text}
        if reply_markup:
            data["reply_markup"] = reply_markup
        payload = json.dumps(data)

        conn = http.client.HTTPSConnection("api.telegram.org", timeout=10)
        conn.request(
            "POST",
            f"/bot{token}/sendMessage",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        success = 200 <= response.status < 300
        conn.close()
        return success
    except Exception:
        # Handle any network errors, timeouts, or unexpected issues gracefully
        return False


def edit_message(chat_id: int, message_id: int, text: str, token: str,
                 reply_markup: dict | None = None) -> bool:
    """Replace a message's text and inline keyboard (no keyboard if None)."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4096],
        "reply_markup": reply_markup or {"inline_keyboard": []},
    }
    return telegram_api("editMessageText", payload, token) is not None


def edit_reply_markup(chat_id: int, message_id: int, reply_markup: dict, token: str) -> bool:
    """Swap only the inline keyboard under a message."""
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
    return telegram_api("editMessageReplyMarkup", payload, token) is not None


def answer_callback(callback_id: str, token: str, text: str | None = None) -> None:
    """Acknowledge a button tap (stops the loading spinner; optional toast text)."""
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:200]
    telegram_api("answerCallbackQuery", payload, token)


def send_chat_action(chat_id: int, action: str, token: str) -> None:
    """Show "typing…" etc. while a slow reply is being prepared."""
    telegram_api("sendChatAction", {"chat_id": chat_id, "action": action}, token)


def download_file(file_id: str, token: str) -> bytes:
    """Download a file the user sent (photo, voice note, …).

    Raises:
        ValueError: If the file is missing or larger than MAX_DOWNLOAD_BYTES.
        OSError: On network failure.
    """
    info = telegram_api("getFile", {"file_id": file_id}, token)
    if not info or not info.get("file_path"):
        raise ValueError("Telegram did not return the file")
    if (info.get("file_size") or 0) > MAX_DOWNLOAD_BYTES:
        raise ValueError("File is too large")

    conn = http.client.HTTPSConnection("api.telegram.org", timeout=20)
    try:
        conn.request("GET", f"/file/bot{token}/{info['file_path']}")
        response = conn.getresponse()
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
        if response.status != 200:
            raise ValueError(f"File download failed: HTTP {response.status}")
    finally:
        conn.close()

    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("File is too large")
    return data

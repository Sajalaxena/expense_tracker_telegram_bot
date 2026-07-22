"""Telegram Bot API helper using stdlib http.client."""

import json
import http.client


def send_telegram_message(chat_id: int, text: str, token: str) -> bool:
    """Send a text message via Telegram Bot API.

    Uses stdlib http.client.HTTPSConnection to POST to the sendMessage endpoint.
    Truncates text to 4096 characters (Telegram's limit).

    Args:
        chat_id: Telegram chat identifier where the message will be sent.
        text: Message text to send (truncated to 4096 chars if longer).
        token: Telegram bot token for authentication.

    Returns:
        True on success, False on any failure. Never raises exceptions.
    """
    try:
        # Truncate to Telegram's 4096 character limit
        if len(text) > 4096:
            text = text[:4096]

        payload = json.dumps({"chat_id": chat_id, "text": text})

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

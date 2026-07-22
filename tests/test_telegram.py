"""Unit tests for lib/telegram.py send_telegram_message helper."""

import json
from unittest.mock import patch, MagicMock

from lib.telegram import send_telegram_message


class TestSendTelegramMessage:
    """Tests for send_telegram_message function."""

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_successful_send_returns_true(self, mock_conn_class):
        """A successful API call (HTTP 200) returns True."""
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_conn.getresponse.return_value = mock_response
        mock_conn_class.return_value = mock_conn

        result = send_telegram_message(12345, "Hello", "bot-token-123")

        assert result is True
        mock_conn.request.assert_called_once_with(
            "POST",
            "/botbot-token-123/sendMessage",
            body=json.dumps({"chat_id": 12345, "text": "Hello"}),
            headers={"Content-Type": "application/json"},
        )
        mock_conn.close.assert_called_once()

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_http_error_returns_false(self, mock_conn_class):
        """A non-2xx HTTP response returns False."""
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 400
        mock_conn.getresponse.return_value = mock_response
        mock_conn_class.return_value = mock_conn

        result = send_telegram_message(12345, "Hello", "token")

        assert result is False

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_network_error_returns_false(self, mock_conn_class):
        """Network errors (connection refused, timeout, etc.) return False."""
        mock_conn_class.side_effect = OSError("Connection refused")

        result = send_telegram_message(12345, "Hello", "token")

        assert result is False

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_timeout_returns_false(self, mock_conn_class):
        """Socket timeout returns False without raising."""
        import socket

        mock_conn = MagicMock()
        mock_conn.request.side_effect = socket.timeout("timed out")
        mock_conn_class.return_value = mock_conn

        result = send_telegram_message(12345, "Hello", "token")

        assert result is False

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_text_truncated_to_4096_chars(self, mock_conn_class):
        """Text longer than 4096 characters is truncated."""
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_conn.getresponse.return_value = mock_response
        mock_conn_class.return_value = mock_conn

        long_text = "A" * 5000
        send_telegram_message(12345, long_text, "token")

        # Extract the body that was sent
        call_args = mock_conn.request.call_args
        sent_body = json.loads(call_args[1]["body"] if "body" in call_args[1] else call_args[0][2])
        assert len(sent_body["text"]) == 4096

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_text_at_exactly_4096_not_truncated(self, mock_conn_class):
        """Text exactly at the limit is sent as-is."""
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_conn.getresponse.return_value = mock_response
        mock_conn_class.return_value = mock_conn

        exact_text = "B" * 4096
        send_telegram_message(12345, exact_text, "token")

        call_args = mock_conn.request.call_args
        sent_body = json.loads(call_args[1]["body"] if "body" in call_args[1] else call_args[0][2])
        assert len(sent_body["text"]) == 4096

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_short_text_not_truncated(self, mock_conn_class):
        """Short text is sent unchanged."""
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_conn.getresponse.return_value = mock_response
        mock_conn_class.return_value = mock_conn

        send_telegram_message(12345, "short msg", "token")

        call_args = mock_conn.request.call_args
        sent_body = json.loads(call_args[1]["body"] if "body" in call_args[1] else call_args[0][2])
        assert sent_body["text"] == "short msg"

    @patch("lib.telegram.http.client.HTTPSConnection")
    def test_unexpected_exception_returns_false(self, mock_conn_class):
        """Any unexpected exception is caught and returns False."""
        mock_conn = MagicMock()
        mock_conn.request.side_effect = RuntimeError("unexpected")
        mock_conn_class.return_value = mock_conn

        result = send_telegram_message(12345, "Hello", "token")

        assert result is False

"""Tests for the /removesub command handler in bot.py."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.bot import cmd_removesub


@pytest.fixture
def mock_update():
    """Create a mock Telegram Update object."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.chat_id = 12345
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    """Create a mock context with args."""
    context = MagicMock()
    context.args = []
    return context


@pytest.mark.asyncio
class TestCmdRemovesub:
    """Tests for cmd_removesub handler."""

    async def test_no_args_shows_usage(self, mock_update, mock_context):
        """When no argument is provided, reply with usage instructions."""
        mock_context.args = []

        await cmd_removesub(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with(
            "Usage: /removesub <name>\nExample: /removesub netflix"
        )

    @patch("src.bot._db")
    @patch("src.bot.regenerate")
    @patch("src.bot._config", {"currency": "₹"})
    @patch("src.bot._export_path", "data.js")
    async def test_removes_matching_subscription(
        self, mock_regenerate, mock_db, mock_update, mock_context
    ):
        """When subscription name matches, deactivate it and confirm."""
        mock_context.args = ["netflix"]
        mock_db.get_active_subscriptions.return_value = [
            {"id": 1, "name": "netflix", "amount": 199, "cycle": "monthly"},
            {"id": 2, "name": "spotify", "amount": 119, "cycle": "monthly"},
        ]
        mock_db.deactivate_subscription.return_value = True

        await cmd_removesub(mock_update, mock_context)

        mock_db.deactivate_subscription.assert_called_once_with(1, 12345)
        mock_update.message.reply_text.assert_called_once_with(
            "✅ Removed subscription: netflix"
        )

    @patch("src.bot._db")
    @patch("src.bot.regenerate")
    @patch("src.bot._config", {"currency": "₹"})
    @patch("src.bot._export_path", "data.js")
    async def test_case_insensitive_match(
        self, mock_regenerate, mock_db, mock_update, mock_context
    ):
        """Case-insensitive match: /removesub Netflix matches 'netflix'."""
        mock_context.args = ["Netflix"]
        mock_db.get_active_subscriptions.return_value = [
            {"id": 3, "name": "netflix", "amount": 199, "cycle": "monthly"},
        ]
        mock_db.deactivate_subscription.return_value = True

        await cmd_removesub(mock_update, mock_context)

        mock_db.deactivate_subscription.assert_called_once_with(3, 12345)
        mock_update.message.reply_text.assert_called_once_with(
            "✅ Removed subscription: netflix"
        )

    @patch("src.bot._db")
    @patch("src.bot._config", {"currency": "₹"})
    @patch("src.bot._export_path", "data.js")
    async def test_no_matching_subscription(
        self, mock_db, mock_update, mock_context
    ):
        """When no active subscription matches the name, reply with error."""
        mock_context.args = ["hulu"]
        mock_db.get_active_subscriptions.return_value = [
            {"id": 1, "name": "netflix", "amount": 199, "cycle": "monthly"},
        ]

        await cmd_removesub(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with(
            "No active subscription found matching 'hulu'"
        )

    @patch("src.bot._db")
    @patch("src.bot._config", {"currency": "₹"})
    @patch("src.bot._export_path", "data.js")
    async def test_no_active_subscriptions_at_all(
        self, mock_db, mock_update, mock_context
    ):
        """When user has no active subscriptions, reply with not found."""
        mock_context.args = ["netflix"]
        mock_db.get_active_subscriptions.return_value = []

        await cmd_removesub(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with(
            "No active subscription found matching 'netflix'"
        )

    @patch("src.bot._db")
    @patch("src.bot.regenerate")
    @patch("src.bot._config", {"currency": "₹"})
    @patch("src.bot._export_path", "data.js")
    async def test_regenerate_called_after_removal(
        self, mock_regenerate, mock_db, mock_update, mock_context
    ):
        """After successful removal, regenerate data.js."""
        mock_context.args = ["spotify"]
        mock_db.get_active_subscriptions.return_value = [
            {"id": 5, "name": "spotify", "amount": 119, "cycle": "monthly"},
        ]
        mock_db.deactivate_subscription.return_value = True

        await cmd_removesub(mock_update, mock_context)

        mock_regenerate.assert_called_once()

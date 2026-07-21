# Tracksy — Telegram Expense Tracker

A personal monthly expense tracker controlled entirely via a Telegram bot. Log spending by sending plain-English messages, view analytics on an offline HTML dashboard. All data stays on your machine.

## Setup

1. **Clone or download** this project
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Create a Telegram bot** — message [@BotFather](https://t.me/BotFather) on Telegram, use `/newbot`, and copy the token
4. **Configure** — open `config.json` and paste your bot token (see [Configuration](#configuration) below)
5. **Run the bot:**
   ```bash
   python -m src.bot
   ```

The bot is now running and will respond to your messages on Telegram.

## Requirements

- Python 3.9+
- pip dependencies:
  - `python-telegram-bot~=21.0`
  - `pytest>=7.0`
  - `hypothesis>=6.80`
  - `pytest-asyncio>=0.21`

## Example Messages

Send any of these to your bot to log an expense:

| Message | Parsed Amount | Category |
|---------|--------------|----------|
| `swiggy 450` | ₹450 | food |
| `rent 1,25,000` | ₹1,25,000 | rent |
| `uber 1.5k` | ₹1,500 | travel |
| `apartment 2l` | ₹2,00,000 | rent |
| `₹500 chai` | ₹500 | food |
| `300rs groceries` | ₹300 | groceries |
| `salary 80k` | ₹80,000 | income |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message explaining how to use the tracker |
| `/help` | Usage instructions with example messages |
| `/total` | Current month's total spend vs budget with a progress bar |
| `/undo` | Delete the most recent transaction |
| `/budget` | Per-category budget caps and current spend |

## Configuration

Edit `config.json` in the project root:

```json
{
  "telegram_token": "YOUR_BOT_TOKEN_HERE",
  "currency": "₹",
  "monthlyBudget": 50000,
  "budgets": {
    "food": 8000,
    "travel": 5000,
    "groceries": 6000,
    "clothes": 3000,
    "rent": 15000,
    "bills": 4000,
    "luxuries": 3000,
    "investments": 10000,
    "health": 3000,
    "education": 2000,
    "other": 2000
  }
}
```

| Field | Description |
|-------|-------------|
| `telegram_token` | Your bot token from @BotFather (required) |
| `currency` | Currency symbol used in replies and the dashboard (default: `₹`) |
| `monthlyBudget` | Your overall monthly spending cap as a positive number |
| `budgets` | Per-category budget caps — set a positive number for each category you want to track against a limit |

> **Note:** `config.json` is in `.gitignore` to prevent accidentally committing your bot token.

## Dashboard

Open `dashboard.html` directly in your browser (double-click or `File → Open`). It works entirely offline via the `file://` protocol — no server needed.

The dashboard loads expense data from `data.js`, which the bot regenerates automatically after every transaction. Sections include:

- Monthly spend vs budget summary with burn bar
- Category breakdown donut chart
- Daily cumulative spend line chart
- Month-over-month comparison
- Per-category budget alerts
- Recent activity feed
- Spending insights

If no data exists yet, the dashboard displays sample data so you can preview the layout.

## Project Structure

```
tracksy/
├── src/
│   ├── bot.py        # Telegram bot (entry point)
│   ├── parser.py     # Message parsing (amount, category, note)
│   ├── db.py         # SQLite database operations
│   ├── export.py     # Generates data.js for the dashboard
│   └── utils.py      # Indian number formatting
├── tests/            # pytest + Hypothesis property-based tests
├── dashboard.html    # Offline analytics dashboard
├── config.json       # Bot token and budget configuration
├── requirements.txt  # Python dependencies
└── data.js           # Auto-generated (consumed by dashboard)
```

## Running Tests

```bash
pytest
```

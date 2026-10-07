# Tracksy — Telegram Expense Tracker

Log spending by messaging a Telegram bot in plain English, by photo, or by voice note. View analytics, AI tips and an AI chat on a password-protected web dashboard. Runs as Vercel serverless functions with data in Supabase.

## Logging from Telegram

| You send | Logged as |
|----------|-----------|
| `swiggy 450` | ₹450 · food |
| `uber 1.5k` | ₹1,500 · travel |
| `rent 1,25,000` | ₹1,25,000 · rent |
| `salary 80k` | ₹80,000 · income |
| `fav 500 headphones` | ₹500 · Personal Favorites (not counted in budget) |
| `swiggy 450, uber 200, chai 30` | three entries (separate with `,` `;` or new lines) |
| `dinner 800 yesterday` | dated yesterday |
| `cab 300 2 days ago` · `chai 30 last monday` · `rent 15k on 1/10` | dated accordingly |
| 📷 a receipt or UPI screenshot | read by Gemini, total logged |
| 🎙️ a voice note — "four fifty swiggy and two hundred uber" | read by Gemini, both logged |

Every confirmation has buttons: **🗑 Delete · 🏷 Category · 📅 −1 day**. The reply also shows the entry's `#id`.

You get budget warnings when a category or the month crosses 80%, and every time something goes over its cap.

### Commands

| Command | Description |
|---------|-------------|
| `/total` | This month's spend vs budget |
| `/budget` | Per-category caps and spend |
| `/setbudget food 8000` | Set a category cap (`0` removes it); `/setbudget total 60000` sets the monthly budget |
| `/undo` | Delete the most recent entry |
| `/delete` | List recent entries with ids; `/delete 123` deletes one |
| `/addsub netflix 649 monthly` | Add a subscription |
| `/removesub netflix` | Remove a subscription |
| `/sub` | List subscriptions and monthly cost |

### Daily job (22:00 IST)

- A reminder if you logged nothing that day.
- Subscription renewals are auto-logged on their renewal day, which is the day of the month (or year) you added the subscription. Each renewal message has a Delete button. A deleted renewal is not logged again for that period.
- On Sundays, a summary of the week vs last week, top categories, biggest spend and month progress.

## Dashboard

`/dashboard.html` (login required) shows a monthly summary, category donut, daily burn, heatmap, month-over-month, budgets, subscriptions and recent activity. Entries and subscriptions can be deleted from the dashboard, **Daily Details** and **Personal Favorites** pages. Use **✏️ Edit** on the Budgets card to change budgets. **Insights** has Gemini money tips and a chat about your spending.

## Setup

1. **Supabase:** create a project and run [supabase/migrations.sql](supabase/migrations.sql) in the SQL editor. It is safe to re-run. On an existing install it only adds the `budgets` table and the `subscriptions.last_billed` column.
2. **Telegram bot:** create one with [@BotFather](https://t.me/BotFather) (`/newbot`).
3. **Deploy to Vercel** and set the environment variables below.
4. **Point the bot at the webhook** (the secret must match `WEBHOOK_SECRET`; `callback_query` is needed for the buttons):
   ```bash
   curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" \
     -d url=https://YOUR-APP.vercel.app/api/webhook \
     -d secret_token=$WEBHOOK_SECRET \
     -d 'allowed_updates=["message","callback_query"]'
   ```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | ✅ | Bot token from @BotFather |
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_KEY` | ✅ | Supabase **service role** key |
| `WEBHOOK_SECRET` | ✅ | Shared secret Telegram sends with each webhook call |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | for dashboard | Dashboard login |
| `SESSION_SECRET` | for dashboard | Random string used to sign login cookies |
| `GEMINI_API_KEY` | for AI | Photo/voice logging, Insights tips and chat |
| `CRON_SECRET` | for daily job | Random string; Vercel sends it to `/api/cron` automatically |
| `TIMEZONE` | | Default `Asia/Kolkata`; decides what "today" means |
| `CURRENCY` | | Default `₹` |
| `MONTHLY_BUDGET` | | Default monthly budget (default `50000`); `/setbudget` and the dashboard override it |
| `BUDGETS` | | Default per-category caps as JSON, e.g. `{"food": 8000, "travel": 5000}`; overridable the same way |

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## Project structure

```
api/                 Vercel serverless functions
  webhook.py         Telegram bot: commands, messages, photos/voice, button taps
  cron.py            Daily job: reminders, subscription renewals, weekly summary
  data.py            Dashboard data (GET)
  delete.py          Delete an entry or subscription (POST)
  budgets.py         Save budgets from the dashboard (POST)
  insights.py        Gemini money tips
  chat.py            Gemini chat about your spending
  login.py, logout.py, page.py   Auth and page serving
lib/
  parser.py          Text → transactions (amounts, categories, multi-entry)
  dates.py           Local time and "yesterday"/"2 days ago" parsing
  entries.py         Entry confirmations with inline buttons
  media.py           Photo/voice → transactions via Gemini
  budget.py          Budget alerts
  db.py              Supabase access
  telegram.py, gemini.py, auth.py, ratelimit.py, config.py, utils.py
templates/           Dashboard pages
supabase/            SQL migrations
tests/               pytest suite
```

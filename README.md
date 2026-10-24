# Tracksy — Telegram Expense Tracker

Log spending by messaging a Telegram bot in plain English, by photo, or by voice note. Track loans and EMIs, and money lent to or borrowed from friends. View analytics, AI tips and an AI chat on a password-protected web dashboard. Runs as Vercel serverless functions with data in Supabase.

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

### Loans & EMIs

Add your loans on the **Loans** page first; the bot matches messages to them by name.

| You send | What happens |
|----------|--------------|
| `home loan emi done` | One EMI of the loan's usual amount is recorded |
| `home loan emi paid 26500` | An EMI of a different amount |
| `home loan 2 emis done` · `home loan emi done yesterday` | Several EMIs / a past date |
| `car loan lumpsum 50000` (also `prepay`, `part payment`) | A prepayment, all of it off the principal |
| `car loan foreclosed` | Pays off whatever is outstanding |

The reply shows a progress bar, EMIs paid/left, the outstanding balance and when the loan ends, with an **↩️ Undo** button. With an interest rate set, each EMI is split into interest (outstanding × rate ÷ 12) and principal. Without one, the outstanding is simply the total still to pay. Recording an EMI doesn't add an expense entry.

### Money with friends

| You send | Logged as |
|----------|-----------|
| `lent rahul 500` · `lent 500 to rahul` · `rahul owes me 500 for dinner` | Rahul owes you ₹500 more |
| `borrowed 1000 from amit` · `i owe amit 1000` · `amit lent me 1k` | You owe Amit ₹1,000 more |
| `rahul paid back 200` · `rahul paid back` (no amount = everything) | Rahul paid you back |
| `paid back amit 500` · `repaid amit` | You paid Amit back |
| `settle rahul` | Clears the balance either way |

Words that also describe ordinary spending (`gave`, `took`, `got`, `received`, `returned`, `paid me`) only count for people already in the ledger, so `gave 500 to maid` is still logged as an expense. Every reply has an **↩️ Undo** button.

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
| `/loans` | Every loan with EMIs paid/left and the outstanding balance |
| `/friends` | Who owes you and whom you owe |
| `/myid` | This chat's id, for `OWNER_CHAT_IDS` |

### Daily job (22:00 IST)

- A reminder if you logged nothing that day.
- Subscription renewals are auto-logged on their renewal day, which is the day of the month (or year) you added the subscription. Each renewal message has a Delete button. A deleted renewal is not logged again for that period.
- On Sundays, a summary of the week vs last week, top categories, biggest spend and month progress.

## Dashboard

`/dashboard.html` (login required) shows a monthly summary, category donut, daily burn, heatmap, month-over-month, budgets, subscriptions and recent activity. Entries and subscriptions can be deleted from the dashboard, **Daily Details** and **Personal Favorites** pages. Subscriptions can also be added (**＋ Add**) and edited (**✏️**) on the dashboard. Use **✏️ Edit** on the Budgets card to change budgets. **Insights** has Gemini money tips and a chat about your spending, loans and friend balances.

- **Loans** (`/loans.html`): add, edit and delete loans. Required fields: name, loan amount, total EMIs, and the EMI amount (or the interest rate, which is used to calculate it). Optional fields: lender, type, interest rate, EMIs already paid, current outstanding (calculated if left blank), EMI day, first EMI date and notes. Each loan shows EMIs paid and left, principal repaid, what's left to pay, the end date, whether this month's EMI is paid or due, and its payment history, where any payment can be undone.
- **Friends** (`/friends.html`): a running balance per person with a paid-back progress bar and full history. Add, edit or delete entries, settle up, rename a person or delete them.

## Setup

1. **Supabase:** create a project and run [supabase/migrations.sql](supabase/migrations.sql) in the SQL editor. It is safe to re-run. On an existing install it only adds what's missing (the `budgets`, `loans`, `loan_payments` and `friend_txns` tables and the `subscriptions.last_billed` column).
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
| `OWNER_CHAT_IDS` | | Comma-separated Telegram chat ids allowed to see and update loans and the friends ledger. If unset, any chat that messages the bot can. Send `/myid` to your bot to get the id to use |
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

`api/` has 12 functions, which is the Vercel Hobby plan's limit per deployment. Add new endpoints to an existing function rather than as a new file.

```
api/                 Vercel serverless functions
  webhook.py         Telegram bot: commands, messages, photos/voice, button taps
  cron.py            Daily job: reminders, subscription renewals, weekly summary
  data.py            Dashboard data (GET)
  delete.py          Delete an entry or subscription (POST)
  subscriptions.py   Add or edit a subscription from the dashboard (POST)
  debts.py           Loans and friends ledger for their pages (GET/POST)
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
  loans.py           EMI maths, loan validation, "home loan emi done" parsing and replies
  friends.py         Friends ledger balances, "lent rahul 500" parsing and replies
  db.py              Supabase access
  telegram.py, gemini.py, auth.py, ratelimit.py, config.py, utils.py
templates/           Dashboard pages
supabase/            SQL migrations
tests/               pytest suite
```

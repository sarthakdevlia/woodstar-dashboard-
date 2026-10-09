# WoodStar Ply Lam — order dashboard

Every order is an order that moves through five steps:
**Order received → Material ordered → Material received → Dispatch → Delivered.**
Each day the owner gives every worker a duty; each worker ticks only the steps of their duty; the
customer follows the card from a link.

Django app with staff logins. Built on the same pattern as the Delta Designs workshop dashboard.

## What's here

| Path | What it is |
|---|---|
| `config/` | Settings and URLs. |
| `staff/` | Accounts (owner / worker), the first-owner bootstrap, sample data for local use. |
| `jobs/` | Orders, steps, daily duties, message wording, audit trail, the API and the customer tracking page. |
| `jobs/stages.py`, `jobs/catalog.py` | **The lists to edit**: the five steps, the product categories and their brands/sizes, the shop's details, the standard message wording. |
| `templates/`, `static/` | Sign-in page, the dashboard page and its script, the customer tracking page. |
| `demo/index.html` | The original clickable demo (browser-only). Not served. |

## Rules it enforces (server-side, `jobs/permissions.py`)

- Two kinds of account: **owner** and **worker**. The owner picks each worker's duty for the day in
  *Today's team*; a new day starts with the previous day's duties.
- A worker ticks only the steps of today's duty, only in order, and can never undo.
- Orders are created by the owner or by whoever is on *Order received* that day.
- Only the owner undoes a step (latest first), sets duties, manages accounts, changes message wording,
  and sees or edits order value and payments. Money is never sent to a worker's browser.
- Every change to an order is written to its audit trail.
- Five failed sign-ins lock that username out for 15 minutes.
- The customer's link (`/t/<token>/`) needs no login, shows only that order, and is not guessable.

## WhatsApp

With the shop's own number connected (see the environment variables below), three things go from
that number through SD Ventures' Gupshup partner account — and nothing else, ever:

- **A thank-you**, by itself, the moment an order is saved.
- **An order update**, only when someone presses the WhatsApp button on an order.
- **A reply** to any customer who messages the number: the step their order has reached, found by
  their phone number. The owner switches this on once, under *WhatsApp messages*.

The first two reach people who have not written first, so WhatsApp has to approve each wording, per
language, as a template. The owner submits them under *WhatsApp messages*, which shows where each
approval stands; a changed wording is a new template, and the approved one keeps going out until the
new one is approved. Every send, and every failure, is written to the order's audit trail.

Without the number connected, the buttons open WhatsApp on the user's own phone with the message
written, and no thank-you is sent. The worker's daily message always works that way.

Each message is charged by WhatsApp and Gupshup. Replies to people who write in are spaced out (one a
minute per phone; "no order found" at most twice a day) so a chatty number cannot run up a bill.

**Who answers the number.** WoodStar's number is answered by the SD Ventures assistant (the AI-SaaS
platform), so the dashboard must not answer it as well. Instead the assistant asks the dashboard:
with `ORDER_STATUS_SECRET` set, `POST /hooks/order-status/` returns the words to send for a phone
number, and the *Switch on* button is replaced by "On". The dashboard's own incoming-message handler
(`/webhooks/whatsapp/`, switched on from *WhatsApp messages*) is only for a number nothing else answers.

Not verified against a live number at the time of writing: the calls follow the clinic platform's
client, parts of which are themselves marked unconfirmed there. The first real submission and send
are the test.

## Customer chats

The shop's WhatsApp conversations live on the SD Ventures platform, which answers the number. The
dashboard shows them under *Customer chats* and sends a person's reply, without keeping a second copy:
`jobs/chats.py` calls the platform's own API (`/api/v1/conversations/`) signed in as one of the shop's
users there (`PLATFORM_EMAIL` / `PLATFORM_PASSWORD`). A reply is recorded on the platform like any staff
reply and quietens the assistant on that chat for a while. Each chat shows that customer's orders, found
by phone number, and can start a new order for them.

Open to the owner and to whoever has *Order received* as their duty that day. WhatsApp allows a free
reply only within 24 hours of the customer's last message; after that the reply box is replaced by a note.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:DJANGO_DEBUG="1"; .venv\Scripts\python manage.py migrate
$env:DJANGO_DEBUG="1"; .venv\Scripts\python manage.py test
$env:DJANGO_DEBUG="1"; .venv\Scripts\python manage.py runserver
```

Locally it uses SQLite. For sample people and orders, set `DEMO_PASSWORD` (8+ characters) and run
`manage.py seed_demo`; every sample account gets that password. It refuses to run on a database that
already has orders.

## Deploy (Render web service)

**Before anything else:** free web services share 750 hours a month across the workspace. With the
clinic API and the Delta Designs dashboard already there, a third free service can exhaust it, and
Render then suspends *all* of them. Either put this service on a paid instance, or host it in a
workspace of WoodStar's own.

1. **Database** — nothing to do by hand. `DATABASE_URL` (step 3) names a database `woodstar` on the
   Postgres instance, and the app creates it itself the first time it starts (`ensure_database`).
   Never point this app at another business's database.
2. **Web service** — New → Web Service → this repo, branch `main`:
   - Runtime: Python · same region as the database · Health check path: `/healthz`
   - Build command: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   - Start command: `python manage.py ensure_database && python manage.py migrate --noinput && python manage.py bootstrap_owner && gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 --access-logfile -`
   - One worker on purpose: the login lockout counter lives in memory.
3. **Environment variables**

   | Name | Value |
   |---|---|
   | `DJANGO_SECRET_KEY` | long random string (Render → Generate) |
   | `ALLOWED_HOSTS` | the dashboard's domain, e.g. `dashboard.example.in` |
   | `DATABASE_URL` | the instance's **Internal** Database URL with its last part changed to `/woodstar` |
   | `OWNER_USERNAME` / `OWNER_NAME` / `OWNER_PASSWORD` | the owner's first login |
   | `PYTHON_VERSION` | `3.12.10` |
   | `FIRST_JOB_NUMBER` | optional; the first order's number (default `1001`) |
   | `WHATSAPP_PARTNER_EMAIL` / `WHATSAPP_PARTNER_SECRET` | the Gupshup partner login, same values as on `sdventures-api` |
   | `WHATSAPP_APP_ID` | the Gupshup app ID of WoodStar's WhatsApp number |
   | `PLATFORM_EMAIL` / `PLATFORM_PASSWORD` | a login of the shop's own on the SD Ventures platform; switches on *Customer chats* |
   | `PLATFORM_URL` | optional; the platform's address (default `https://www.sdventure.in`) |
   | `ORDER_STATUS_SECRET` | a long random string (Generate); the same value goes on `sdventures-api` |
   | `WHATSAPP_WEBHOOK_SECRET` | only if the dashboard itself answers the number; not needed with the assistant |

   Once the owner has signed in, **delete `OWNER_PASSWORD`** — the account already exists and the
   command never overwrites it.
4. **Domain** — Settings → Custom Domains → add the dashboard's domain, then add the CNAME Render shows
   at the registrar.
5. **First day** — the owner signs in, adds each worker under *Staff & logins*, then gives them their
   duty in *Today's team*.

Site and dashboard by SD Ventures.

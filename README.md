# AMUECSF Anonymous Counseling Bot (የAMUECSF ሚስጥራዊ የምክር አገልግሎት)

Production-conscious, lightweight, and privacy-first Amharic Telegram bot built for **Arba Minch University Evangelical Christian Students Fellowship (AMUECSF)**.

---

## ⚠️ Important Disclaimer & Limitations

> [!IMPORTANT]
> This service is a **peer fellowship spiritual support and prayer tool**, **NOT** professional psychotherapy, medical counseling, or an emergency service.
> 
> In acute crisis, life-threatening danger, or severe mental distress:
> - 🚑 **Ethiopia Free Medical Emergency / Ambulance:** `907`
> - 🚓 **Police Emergency:** `991`
> - 🏥 **Arba Minch General Hospital:** Emergency Department (በአካል ይቅረቡ)
> - 🏫 **AMU Student Clinic:** Visit your campus clinic immediately

---

## 🔒 Architectural & Security Highlights

1. **Zero Message Persistence**: Counseling message contents are relayed purely in-memory via Telegram's `send_message` API. Message bodies, transcripts, and media are **never written to the database or logged**.
2. **Strict Anonymity**: Students are identified to counselors only by an internal anonymous ID (`ተማሪ #<session_id>`). Student usernames and numeric Telegram IDs are **never exposed to counselors**.
3. **Atomic First-Acceptance Matching**: Employs conditional atomic SQL transactions (`UPDATE ... WHERE status = 'WAITING'`) to guarantee that concurrent acceptances by multiple counselors deterministically produce **exactly one winner**.
4. **Fail-Closed Crisis Detection**: Every message is scanned against deterministic Amharic and English crisis regex patterns. If detected or if any error occurs during evaluation, the system fails closed: flags the session, alerts leaders, and delivers verified emergency contacts to the student immediately.
5. **Dual-Mode Operation**: Controlled by `BOT_MODE`:
   - `polling`: Zero-tunnel local development.
   - `webhook`: Production mode on Render with HTTPS and `X-Telegram-Bot-Api-Secret-Token` validation.
6. **Restart Resilience**: Active session state (`WAITING`, `ACTIVE`, `CLOSED`) and user permissions reside in PostgreSQL; ongoing conversations survive service restarts.

---

## 📁 Project Structure

```text
amuecsf-bot/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application & lifespan manager
│   ├── config.py        # Pydantic Settings & environment validation
│   ├── bot.py           # Telegram Application & handler registrations
│   ├── handlers.py      # Command, callback, & text router handlers
│   ├── keyboards.py     # Inline & Reply keyboards in Amharic
│   ├── database.py      # SQLAlchemy 2.0 async engine & sessionmaker
│   ├── models.py        # Typed SQLAlchemy database models (no message storage)
│   └── services/
│       ├── __init__.py
│       ├── matching.py  # Atomic request queueing & counselor claiming
│       ├── messaging.py # Ephemeral relaying & crisis alert dispatch
│       └── crisis.py    # Deterministic Amharic/English safety evaluation
├── tests/
│   ├── __init__.py
│   ├── conftest.py      # Test database engine, fixtures & mocks
│   └── test_bot_flows.py# Automated tests for all 7 required workflows
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 🛠️ Step-by-Step Setup Guide

### 1. Telegram Bot Setup (@BotFather)

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to choose a display name and username (e.g. `amuecsf_counseling_bot`).
3. Save the HTTP API token provided by BotFather (this is your `TELEGRAM_BOT_TOKEN`).
4. (Recommended) Configure bot privacy:
   - Send `/setprivacy` to `@BotFather`
   - Select your bot -> choose **ENABLE** (default).
5. (Optional) Set description and commands:
   - `/setdescription`: `የAMUECSF ሚስጥራዊ እና ደህንነቱ የተጠበቀ የምክር አገልግሎት ቦት`
   - `/setcommands`:
     ```text
     start - አገልግሎቱን ጀምር (Start)
     about - ስለ አገልግሎቱ (About)
     emergency - የአደጋ ጊዜ ስልኮች (Emergency Contacts)
     ```

### 2. Neon PostgreSQL Database Setup

1. Sign up or log in at [Neon Console](https://console.neon.tech/).
2. Create a new project (e.g. `amuecsf-counseling-db`).
3. Under **Dashboard -> Connection Details**, choose **SQLAlchemy** or **Connection String**.
4. Copy the connection string. It will look like:
   ```text
   postgresql://<user>:<password>@<ep-xyz>.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   *(The bot automatically converts `postgresql://` to `postgresql+asyncpg://` and configures SSL parameters).*

### 3. Local Environment Configuration

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and fill in your values:
   ```dotenv
   BOT_MODE=polling
   ENVIRONMENT=development
   LOG_LEVEL=INFO

   TELEGRAM_BOT_TOKEN=your_botfather_token_here
   DATABASE_URL=postgresql+asyncpg://user:password@host/dbname?sslmode=require
   LEADER_IDS=your_telegram_numeric_id
   CRISIS_ALERT_CHAT_ID=
   ```

*(Tip: To get your Telegram numeric user ID, message [@userinfobot](https://t.me/userinfobot) on Telegram).*

---

## 🚀 Running Locally (Phase 1)

1. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Start the application**:
   ```bash
   uvicorn app.main:app --reload
   ```

4. Verify health endpoint:
   Open [http://localhost:8000/health](http://localhost:8000/health). You should see:
   ```json
   {
     "status": "ok",
     "mode": "polling",
     "environment": "development"
   }
   ```

5. Test the bot on Telegram by sending `/start`.

---

## 🧪 Automated Testing (Phase 2)

Run the full automated test suite with pytest:

```bash
pytest -v
```

The test suite validates:
- **Consent Enforcement**: Unconsented users are blocked from counseling actions until agreement.
- **Anonymous Identity Protection**: Student Telegram IDs and usernames are never transmitted or logged.
- **Counselor Matching & Concurrency**: Atomic SQL update ensures only one counselor can claim a request.
- **Message Routing & Ownership**: Messages are strictly forwarded between session participants; non-participants are blocked.
- **Unauthorized Commands**: Leader commands (`/add_counselor`, `/remove_counselor`, `/list_counselors`) are forbidden to unauthorized users.
- **Crisis Detection & Fail-Closed Behavior**: Amharic and English danger phrases trigger human alerts and emergency contacts; malformed inputs fail closed.
- **Session Cleanup**: Closing sessions resets status to `CLOSED`, clears active pointers, and notifies participants.

---

## ☁️ Render Deployment Guide (Phase 3)

Deploy the bot as a **Single Web Service** on Render with zero background worker costs.

### 1. Push Code to GitHub

Make sure your repository has your latest code and `.env` is **NOT** committed (it is ignored by `.gitignore`).

### 2. Create Render Web Service

1. Log into your [Render Dashboard](https://dashboard.render.com/).
2. Click **New +** -> **Web Service**.
3. Select your repository.
4. Configure service settings:
   - **Name**: `amuecsf-counseling-bot`
   - **Region**: Frankfurt (EU Central) or Ohio (US East)
   - **Branch**: `main`
   - **Root Directory**: *(leave blank)*
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**:
     ```bash
     uvicorn app.main:app --host 0.0.0.0 --port $PORT
     ```

### 3. Configure Environment Variables on Render

In the **Environment** tab of your Render service, add the following variables:

| Key | Value / Example | Note |
|---|---|---|
| `BOT_MODE` | `webhook` | Enables production webhook mode |
| `ENVIRONMENT` | `production` | Production environment tag |
| `LOG_LEVEL` | `INFO` | Standard logging |
| `TELEGRAM_BOT_TOKEN` | `123456789:ABC...` | From @BotFather |
| `WEBHOOK_URL` | `https://amuecsf-counseling-bot.onrender.com` | Your Render service URL |
| `WEBHOOK_SECRET_TOKEN` | `generate-random-32-chars-alphanumeric` | Secret for header verification |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Neon PostgreSQL pooled URL |
| `LEADER_IDS` | `111111111,222222222` | Numeric Telegram IDs of fellowship leaders |
| `CRISIS_ALERT_CHAT_ID` | `-1001234567890` *(optional)* | Channel/Group ID for crisis alerts |

### 4. Deploy & Verify

1. Click **Deploy Web Service**.
2. Render builds and launches the application.
3. During startup, the bot automatically registers its webhook endpoint at:
   `https://<your-service>.onrender.com/telegram-webhook`
   with your configured secret token.
4. Check the Render logs to confirm:
   ```text
   INFO: Webhook registered at: https://<your-service>.onrender.com/telegram-webhook
   ```
5. Test the `/health` endpoint:
   `https://<your-service>.onrender.com/health`

### ℹ️ Free-Tier Hosting Limitations Note
- Render free tier instances spin down after 15 minutes of inactivity. When a message arrives, Render wakes up the service (causing an initial 30-50 second cold-start delay).
- For a 24/7 uninterrupted fellowship counseling experience, upgrade to Render's **Starter** instance ($7/month) or use a periodic uptime health check ping.

---

## 👥 Leader & Counselor Operational Commands

| Command | Allowed Role | Description |
|---|---|---|
| `/start` | Everyone | Welcomes user, presents consent disclaimer or dashboard |
| `/about` | Everyone | Explains ministry mission, privacy principles, and limitations |
| `/emergency` | Everyone | Displays emergency phone numbers (Ambulance, Hospital, Police) |
| `/add_counselor <telegram_id>` | Leaders only | Grants user the Counselor role |
| `/remove_counselor <telegram_id>` | Leaders only | Revokes Counselor approval |
| `/list_counselors` | Leaders only | Displays list of approved counselors and availability |

---

## 📜 License
Developed with love for the ministry of **Arba Minch University Evangelical Christian Students Fellowship (AMUECSF)**.

# Job Tracker - Application Summary

## Overview
Job Tracker is a desktop application that automatically fetches job application emails from Gmail and classifies them into a structured database using AI/LLM models. It provides a clean UI to browse, manage, and export job applications.

**Current Status:** Stable & Functional ✅

---

## Core Features

### 1. Gmail Integration
- **Authentication:** OAuth2 with secure token storage
- **Email Fetching:** Retrieves both received (Primary inbox) and sent emails
- **Token Storage:** 
  - Primary: OS Keyring (macOS Keychain, Windows Credential Manager, Linux libsecret)
  - Fallback: Encrypted file (`~/.../JobTracker/token.json`)
- **Scope:** Read-only access to Gmail
- **Reconnection:** Users can clear token and re-authenticate via "Switch Account" button

### 2. Email Classification
Supported LLM Providers:
- **OpenAI** (gpt-4o-mini, gpt-4, etc.) ✅
- **Anthropic** (Claude models) ✅
- **Google Gemini** ✅
- **Local Ollama** ✅

**Classification Logic:**
- Analyzes email subject, sender, body, and direction (received/sent)
- Extracts: company name, job role, application status
- Assigns status: Applied | Under Review | Interview | Offered | Rejected | Other
- Filters out non-job emails (newsletters, alerts, etc.)

**API Key Storage:**
- Primary: OS Keyring (secure, native)
- Fallback: Encrypted file (`~/.../JobTracker/api_key.enc`)
- Never stored as plaintext on disk
- Can be set via environment variable (`MODEL_API_KEY`) as override

### 3. Job Database
**Storage:** SQLite database (`~/.../JobTracker/jobs.db`)

**Fields per Job:**
- ID (auto-incremented)
- Company name
- Job role/title
- Application status
- Application date (YYYY-MM-DD)
- Last updated timestamp

**Operations:**
- Create/Read/Update/Delete jobs manually via UI
- Bulk import from Gmail pipeline
- Deduplication: Prevents duplicate (company, role) pairs
- Status priority system: Applies intelligent upgrades (e.g., "Applied" → "Interview")

### 4. Email Processing Pipeline
**Run Modes:**
1. **Resume from checkpoint** — Processes emails since last successful run
2. **Last N hours** — Processes emails from last X hours (default: 36)
3. **Since date** — Processes emails from specified date onwards

**Process:**
1. Fetches emails from Gmail
2. Marks each as processed (prevents re-processing)
3. Sends to LLM classifier
4. Extracts job details
5. Upserts to database (creates or updates)
6. Exports to JSON and CSV
7. Updates checkpoint timestamp

**Limits:**
- Max 1000 emails per run (prevents OOM on large mailboxes)
- Configurable LLM timeouts
- Per-email delay configurable (for Ollama)

### 5. User Interface

**Pages:**
1. **Jobs Page** (default)
   - Table view of all jobs
   - Search by company/role
   - Filter by status
   - Sort by any column
   - Manual add/edit/delete
   - View count and filters applied

2. **Setup Page**
   - Gmail account display with reconnect option
   - Model provider selection (toggle: Local/OpenAI/Gemini/Anthropic)
   - Provider-specific config:
     - Ollama: endpoint URL + model name
     - API-based: API key + model name
   - Test connection button
   - Run mode selection (checkpoint/hours/date)
   - Run Now button with progress display
   - Delete All Jobs (with confirmation)

**Header:**
- App title with authenticated Gmail email
- Theme toggle (light/dark)
- Setup button

**Progress UI:**
- Shows during pipeline run
- Displays: processed count, total count, breakdown (new/updated/skipped)
- Stop button to cancel run
- Auto-hides on completion

**Security:**
- CSRF tokens on all state-changing requests
- Session-based with 24-hour expiration
- API keys never sent in responses (masked as ••••••••)

### 6. Data Export
- **CSV Export:** `~/.../JobTracker/jobs.csv` (safe with quote escaping)
- **JSON Export:** `~/.../JobTracker/jobs_data.json` (includes checkpoint, stats)
- Auto-exported after each pipeline run or manual job change

---

## Platform Support

### macOS
- ✅ Native .app bundle (arm64 + x86_64)
- ✅ Keychain integration for token/API key storage
- ✅ System tray icon (right-click menu)
- ✅ Auto-launch via drag-to-Applications
- ✅ Stays in dock while running

### Windows
- ✅ Standalone .exe
- ✅ Credential Manager integration
- ✅ System tray icon
- ✅ Installer-ready

### Linux
- ✅ Standalone executable
- ✅ libsecret integration (GNOME Keyring)
- ✅ System tray icon
- ✅ Encrypted file fallback

---

## File Structure

**Application Directory (~/.../JobTracker/):**
```
jobs.db              ← SQLite database (jobs table, processed_emails table, metadata)
token.json           ← Gmail OAuth token (encrypted via keyring/Fernet)
api_key.enc          ← LLM API key (encrypted via Fernet)
config.json          ← Non-sensitive config (provider, model, endpoint)
jobs.csv             ← CSV export (auto-generated)
jobs_data.json       ← JSON export (auto-generated)
```

**Application Bundle (macOS):**
```
Job Tracker.app/Contents/Resources/
├── launcher.py
├── server.py
├── main.py
├── database.py
├── gmail_client.py
├── ollama_classifier.py
├── api_key_storage.py
├── config.py
├── index.html
└── credentials.json
```

---

## API Endpoints

**Static:**
- `GET /` — Serve index.html with CSRF token

**Jobs:**
- `GET /api/jobs` — Fetch all jobs
- `POST /api/jobs` — Create job
- `PUT /api/jobs/<id>` — Update job
- `DELETE /api/jobs/<id>` — Delete job
- `DELETE /api/jobs` — Delete all jobs

**Config:**
- `GET /api/config` — Fetch current config (API key masked)
- `POST /api/config` — Save config (API key to secure storage)
- `POST /api/test-connection` — Test LLM connection

**Pipeline:**
- `POST /api/run` — Start email processing
- `POST /api/stop` — Cancel running pipeline
- `GET /api/progress` — Poll pipeline progress
- `GET /api/checkpoint` — Get last checkpoint timestamp

**Gmail:**
- `GET /api/gmail-user` — Get authenticated email
- `POST /api/gmail-reconnect` — Clear Gmail token

**Security:**
- `GET /api/csrf-token` — Get CSRF token for session

---

## Security Features

1. **CSRF Protection**
   - Session-based tokens
   - Required on all POST/PUT/DELETE requests
   - 24-hour expiration

2. **API Key Storage**
   - Never plaintext on disk
   - Encrypted via OS keyring (primary)
   - Encrypted via Fernet (fallback)
   - Environment variable override support

3. **Gmail Token Storage**
   - Same secure approach as API key
   - Auto-refreshed when expired
   - User can clear via UI for re-authentication

4. **JSON Validation**
   - Pydantic schemas for all request bodies
   - Status validation against whitelist
   - Type checking

5. **CSV Injection Prevention**
   - All CSV fields quoted
   - Formula prefix characters safe

---

## Configuration

**Environment Variables (optional overrides):**
```bash
MODEL_PROVIDER=openai              # ollama, openai, gemini, anthropic
MODEL_API_KEY=sk-...               # API key for non-Ollama providers
MODEL_NAME=gpt-4o-mini             # Model name/ID
OLLAMA_ENDPOINT=http://localhost:11434/api/generate
JOB_TRACKER_DB=/path/to/jobs.db
GMAIL_CREDENTIALS=/path/to/credentials.json
GMAIL_TOKEN=/path/to/token.json
JOB_TRACKER_API_KEY_FILE=/path/to/api_key.enc
LOG_LEVEL=INFO
```

**UI-Configurable:**
- LLM provider
- Model name
- Ollama endpoint
- API key (stored securely)
- Run mode (checkpoint/hours/date)

---

## Known Behaviors

✅ **Working:**
- Gmail OAuth flow with local browser callback
- Email fetching and deduplication
- LLM classification via all 4 providers
- Job CRUD operations
- Database persistence
- Progress tracking and cancellation
- Theme toggle (light/dark)
- Secure token/API key storage
- CSV/JSON exports
- Multi-platform support
- System tray with context menu
- Browser reopenable without app restart

✅ **Tested:**
- Clearing Gmail token and re-authenticating
- Storing API keys via UI (survives app restart)
- Processing 39+ emails in single run
- Manual job add/edit/delete
- Checkpoint resumption
- Closing browser without closing app
- Opening browser from tray menu

---

## Dependencies

**Core:**
- Flask (web server)
- Pydantic (request validation)
- SQLite3 (database)
- google-auth-oauthlib (Gmail OAuth)
- google-auth-httplib2 (Gmail API)
- google-api-python-client (Gmail API)
- requests (HTTP)

**LLM Providers:**
- openai (OpenAI API)
- anthropic (Anthropic API)
- google-generativeai (Gemini API)

**Security:**
- cryptography (Fernet encryption)
- keyring (OS keyring access)

**Desktop:**
- tkinter (system tray, bundled with Python)
- pyinstaller (bundling)

---

## Typical User Workflow

1. **First Launch:**
   - Install app (drag .app to Applications on macOS)
   - Open app → browser opens to http://localhost:8080
   - Click "Setup"
   - Choose LLM provider (e.g., OpenAI)
   - Enter API key
   - Click "Test Connection"
   - If successful, click "Save"

2. **Gmail Authentication:**
   - Still on Setup, click "Switch Account" or refresh
   - Browser opens Google OAuth flow
   - User logs in, grants permission
   - Token saved to keyring automatically

3. **First Run:**
   - Select run mode (default: last 36 hours)
   - Click "Run Now"
   - Progress bar shows: "Processing emails…"
   - On completion: "39 new · 0 updated · 0 skipped"
   - Click "Jobs" to view

4. **Ongoing Usage:**
   - Jobs page shows all applications
   - Search/filter as needed
   - Manually add jobs or run pipeline periodically
   - Close browser, app stays running in tray
   - Right-click tray icon → "Open" to reopen browser

5. **Data Persistence:**
   - All jobs saved to local SQLite DB
   - API key and Gmail token encrypted
   - Settings saved to config.json
   - CSV/JSON exports available in app data directory

---

## Troubleshooting

**"API key not found" error:**
- Enter API key in Setup → save
- Verify provider is correct (OpenAI for sk-... keys)
- Test connection before running pipeline

**"Gmail token cleared":**
- Refresh page
- Click "Switch Account"
- Re-authenticate with Google

**"All emails skipped":**
- Run pipeline with fresh time range
- Check LLM connection is working (Test Connection button)
- Review email subjects and content match job-related keywords

**App closes when browser closes:**
- This is fixed in current version
- Right-click tray icon to reopen browser
- App stays in tray even with browser closed

---

## Next Steps / Future Enhancements

- [ ] Desktop notifications on new applications
- [ ] Advanced filtering and analytics
- [ ] Email archive/sync history
- [ ] Webhook support for third-party integrations
- [ ] Custom classification rules
- [ ] Interview date tracking with calendar sync
- [ ] Statistics dashboard
- [ ] Auto-run on schedule
- [ ] Data backup and restore

---

**Last Updated:** June 14, 2026  
**Version:** 1.0.0  
**Status:** Production Ready  
**License:** MIT (assumed)

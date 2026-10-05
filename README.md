# Availability Change Alert Sender

Python monitor for Amazon, Target, Walmart, and other product pages. It checks products every 30 seconds (configurable) and sends a Telegram message when a product is detected as available.

## What It Does

- Tracks multiple Amazon, Target, Walmart, and other product URLs.
- Uses browser-based detection (Playwright) for dynamic pages.
- Treats `Add to cart` or `Buy now` as availability signals.
- Stores last known state in `.state.json`.
- Sends alert on unavailable -> available transitions and pauses that URL for 10 minutes before checking it again.
- Lets you configure an optional local start time in the dashboard. If set, pressing `Start` waits until that local computer time before loading pages.
- Supports staged automation per URL: auto add-to-cart, auto checkout, and optional auto PayPal final submit.

## Quick Start

1. Create a virtual environment.
2. Install dependencies.
3. Copy `.env.example` to `.env`.
4. Set Telegram values.
5. Run the monitor.

### Verified Windows Setup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Then run one of:

```powershell
python main.py
python main.py --web
python main.py --test-telegram
```

### Common First-Run Issues (Windows)

1. `Activate.ps1` is blocked by execution policy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

2. Playwright browser not installed or missing after dependency install:

```powershell
python -m playwright install chromium
```

3. Chrome debug port conflict when using CDP (`127.0.0.1:9222`):

- Close existing Chrome debug sessions and restart Chrome with `--remote-debugging-port=9222`.
- Or switch to local profile mode with `BROWSER_USER_DATA_DIR` and clear `BROWSER_CDP_URL` in `.env`.

4. Telegram test does not send:

- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` or `app_settings.json`.
- Re-run:

```powershell
python main.py --test-telegram
```

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Run Commands

Both modes now use `app_settings.json` as the single runtime configuration source.
Environment variables in `.env` are only used to hydrate missing values on first run.

Run monitor:

```powershell
python main.py
```

Run local dashboard (web UI):

```powershell
python main.py --web
```

Then open http://127.0.0.1:8000 in your browser.

Test Telegram only:

```powershell
python main.py --test-telegram
```

## Local Dashboard Features (Initial Implementation)

- Start and stop monitoring from the UI.
- View current link status, reason, and next check countdown.
- Add and delete monitored links.
- Edit and save core settings in-app:
  - Telegram bot token
  - Telegram chat id
  - Check interval
  - Optional local start time after pressing `Start`
  - Alert cooldown
- Settings are stored in `app_settings.json` (created automatically on first run).
- Store-specific tables for Amazon, Target, Walmart, and Other links.
- Per-link cart automation controls now include:
  - Auto add to cart
  - Auto checkout
  - Auto PayPal payment (final submit)
  - Max price threshold

Walmart note:

- Walmart USA monitoring is supported in this release.
- Walmart auto add-to-cart / checkout is not enabled yet.

Notes:

- The legacy CLI monitor mode still works with `python main.py`.
- On first run, if `.env` exists, current settings are hydrated into `app_settings.json`.
- After that, update settings from the web UI (or by editing `app_settings.json`) and both CLI and web will use the same values.

## Auto Checkout and PayPal (Target)

You can enable automation per URL in this sequence:

1. Enable `Auto add`.
2. Enable `Auto checkout`.
3. Enable `Auto PayPal pay` for full end-to-end attempt.

Execution stages:

- `added_to_cart`
- `open_checkout`
- `select_paypal`
- `paypal_auth`
- `payment_confirm`
- `order_submitted`

Important safety constraints:

- Full auto PayPal requires browser UI (`BROWSER_HEADLESS=false`).
- Full auto PayPal requires a persistent authenticated session (`BROWSER_CDP_URL` or `BROWSER_USER_DATA_DIR`).
- If PayPal/Target triggers captcha, MFA, security challenge, or session expiry, the flow aborts and reports the stage/error.
- No MFA/captcha bypass is implemented.

## Configuration Reference (.env)

Required:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: Chat or group id.

Core options:

- `CHECK_INTERVAL_SECONDS=30`: Polling interval.
- `ALERT_RECHECK_COOLDOWN_SECONDS=600`: Seconds to wait before rechecking a URL after an availability alert.
- `TARGET_URLS=...`: Comma-separated URLs. If empty, defaults are used.
- `STATE_FILE=.state.json`: State persistence file.
- `LOG_LEVEL=INFO`: Logging level.

Browser options:

- `CHECKER_ENGINE=browser`: Use browser checker.
- `BROWSER_HEADLESS=false`: Show browser (`false`) or run hidden (`true`).
- `BROWSER_TIMEOUT_SECONDS=30`: Timeout per page action.
- `BROWSER_CHANNEL=chrome`: Browser channel.
- `BROWSER_USER_DATA_DIR=`: Optional profile path (local launch mode).
- `BROWSER_CDP_URL=http://127.0.0.1:9222`: Attach to an already-open Chrome debug session.

Shipping ZIP enforcement:

- `EXPECTED_SHIP_ZIP=12345,67890`: Accept one or multiple ZIPs (comma-separated).
- `ENFORCE_SHIP_ZIP=true`: Require one expected ZIP to appear in page header context.

Notes:

- Keep either `BROWSER_CDP_URL` or `BROWSER_USER_DATA_DIR` configured for stable Target behavior.
- If both are set, CDP is preferred when available.

## Common Changes (Step by Step)

### 1) Change check interval

Edit `.env`:

```env
CHECK_INTERVAL_SECONDS=45
```

Restart:

```powershell
python main.py
```

### 2) Add or remove monitored URLs

Edit `.env` and set `TARGET_URLS` as comma-separated URLs:

```env
TARGET_URLS=https://www.target.com/p/item-1,https://www.target.com/p/item-2
```

Restart the app.

### 3) Accept one or many shipping ZIPs

Edit `.env`:

```env
EXPECTED_SHIP_ZIP=12345,67890
ENFORCE_SHIP_ZIP=true
```

If you do not want ZIP validation, set:

```env
ENFORCE_SHIP_ZIP=false
```

### 4) Use your existing Chrome session (recommended)

Start Chrome in debug mode:

```text
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebugProfile"
```

Set in `.env`:

```env
BROWSER_CDP_URL=http://127.0.0.1:9222
```

Then run `python main.py`.

### 5) Use local browser launch instead of CDP

Clear CDP and set user data dir:

```env
BROWSER_CDP_URL=
BROWSER_USER_DATA_DIR=C:\Users\<your_user>\AppData\Local\Google\Chrome\User Data
```

Important: close all Chrome windows first to avoid profile lock issues.

### 6) Force a fresh availability baseline

Delete state file and restart:

```powershell
Remove-Item .state.json -ErrorAction SilentlyContinue
python main.py
```

## Troubleshooting

- `connect ECONNREFUSED 127.0.0.1:9222`:
  Chrome was not started with `--remote-debugging-port=9222`, or it was closed.
- `Target page, context or browser has been closed`:
  The attached browser/context was closed; restart Chrome debug session and rerun.
- Repeated `ship_zip_mismatch...`:
  Adjust `EXPECTED_SHIP_ZIP`, set `ENFORCE_SHIP_ZIP=false`, or change Target ship-to location in Chrome.

## Current Defaults

- Check interval: 30 seconds.
- Alert recheck cooldown: 600 seconds (10 minutes).
- Engine: browser.
- ZIP enforcement: enabled.
- Accepted ZIPs example: `12345,67890`.

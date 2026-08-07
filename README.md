# GMF CMP Monitor

Automated real-time monitoring and alert pipeline for the Telkomsel CMP (Connectivity Management Platform) Dashboard at GMF AeroAsia.

Monitors the CMP Dashboard state continuously, auto-refreshes periodically, handles session expirations gracefully, and automatically manages VPN connectivity to bridge between corporate email (IMAP OTP retrieval) and public Cloudflare WARP (bypassing intranet firewall blocking on the Vaadin SPA).

---

## Features

- **Automated Authentication & Dual-Factor OTP**:
  - CAS single-sign-on login using stored credentials.
  - Automated OTP retrieval via IMAP directly from the corporate GMF mailbox.
  - Resilient token parsing with clock-skew protection.
- **Dynamic VPN Orchestration**:
  - **Office / Check Point VPN**: Ensures reachability to the internal IMAP mailbox for OTP reception.
  - **Cloudflare WARP Transition**: Automatically connects to WARP after OTP submission to bypass corporate firewall restrictions on Telkomsel's Vaadin 7 SPA portal.
  - **Automatic Reauthentication Cycle**: If a session expires during continuous monitoring, WARP is temporarily disconnected, the office route is restored to fetch a new OTP, and WARP is re-engaged.
  - **Safety & Non-Destructive**: Never disconnects pre-existing tunnels or unrelated VPN interfaces (e.g. Tailscale).
- **Vaadin 7 SPA State Machine**:
  - Reliable menu navigation avoiding invalid direct-URL hash changes (`#!dashboard`).
  - DOM stabilization checks, multi-candidate container locators, timeout recovery, and automatic page reloads.
- **Designed for Monitoring Displays (NOC / Office Screens)**:
  - Runs in headed browser mode (`HEADLESS=false`) by default so the live dashboard is visible to team operators.
  - One-click batch scripts (`setup.bat` and `start_monitor.bat`) for portable and quick deployment.

---

## Quick Start (Portable Office PC Deployment)

For deploying on a display PC in the office or NOC:

### 1. Requirements
- **Windows 10 / 11 (64-bit)**
- **Python 3.10+** (Make sure to check *"Add python.exe to PATH"* during installation)
- **Cloudflare WARP Client**: Free consumer client installed from [1.1.1.1](https://1.1.1.1/)

### 2. One-Click Setup
Double-click **`setup.bat`**:
- Installs required Python dependencies (`playwright`, `tzdata`).
- Downloads the Playwright Firefox driver automatically.
- Creates `.env` from template if not already present.

### 3. Configure Credentials
Open `.env` in Notepad and verify your credentials:
- `CMP_USERNAME` & `CMP_PASSWORD`: Telkomsel CMP portal credentials.
- `IMAP_USERNAME` & `IMAP_PASSWORD`: Corporate GMF mailbox credentials for OTP retrieval.
- `HEADLESS=false`: Displays the browser live on screen.

### 4. Launch Monitoring
Double-click **`start_monitor.bat`**:
- The browser window will open automatically, perform authentication, retrieve OTP, switch to WARP, and display the live dashboard with periodic 60-second refreshes.
- Press `Ctrl + C` in the console window to stop cleanly at any time.

---

## Project Structure

```text
├── config.py                 # Pydantic/dataclass environment & settings validator
├── cmp_auth.py               # CAS login & OTP submission handler
├── dashboard_monitor.py      # Vaadin SPA dashboard monitoring state machine
├── imap_client.py            # Secure IMAP adapter for OTP retrieval
├── network_probe.py          # Reachability probe (DNS/TCP/TLS)
├── network_diag.py           # Non-destructive network position diagnostics
├── vpn.py                    # Check Point & Cloudflare WARP subprocess adapters
├── main.py                   # Main process entrypoint & coordinator
├── setup.bat                 # One-click installation script for Windows
├── start_monitor.bat         # One-click launcher for Windows
├── requirements.txt          # Python dependencies
├── logs/                     # Timestamped runtime logs (ignored by git)
└── tests/                    # Offline unit tests & opt-in live workflow tests
```

---

## Configuration Reference (`.env`)

```env
# CMP Portal URLs
CMP_CAS_URL=https://ep.iotcc.telkomsel.com/cas/login?service=https%3A%2F%2Fep.iotcc.telkomsel.com%2Fcas%2Foauth2.0%2FcallbackAuthorize%3Fclient_id%3DenterprisePortal%26redirect_uri%3Dhttps%253A%252F%252Fep.iotcc.telkomsel.com%26response_type%3Dcode%26client_name%3DCasOAuthClient
CMP_PRODUCTS_URL=https://ep.iotcc.telkomsel.com/#!products
CMP_DASHBOARD_URL=https://ep.iotcc.telkomsel.com/#!dashboard

# Credentials
CMP_USERNAME=your_cmp_username
CMP_PASSWORD=your_cmp_password

# IMAP Configuration
IMAP_HOST=mail.gmf-aeroasia.co.id
IMAP_PORT=993
IMAP_USERNAME=your_email@gmf-aeroasia.co.id
IMAP_PASSWORD=your_email_password
IMAP_TLS_MODE=imaps
IMAP_VERIFY_TLS=true
IMAP_MAILBOX=INBOX
OTP_SUBJECT=CMP - YOUR TOKEN

# Browser & Refresh Settings
HEADLESS=false
REFRESH_INTERVAL_SECONDS=60
NAVIGATION_TIMEOUT_MS=90000
OTP_FORM_TIMEOUT_MS=60000
LOG_LEVEL=INFO

# Cloudflare WARP Client
WARP_CLI_PATH="C:/Program Files/Cloudflare/Cloudflare WARP/warp-cli.exe"
WARP_MODE=warp

# (Optional: Only when running outside office network)
CHECKPOINT_SITE=VPN-GMF
CHECKPOINT_GATEWAY_NAME=GMFINETFW01
CHECKPOINT_AUTH_MODE=credentials
CHECKPOINT_USERNAME=your_checkpoint_user
CHECKPOINT_PASSWORD=your_checkpoint_pass
CHECKPOINT_TRAC_PATH="C:/Program Files (x86)/CheckPoint/Endpoint Connect/trac.exe"
```

---

## Running Offline Tests

Run the complete test suite (370+ unit & regression tests) without network or live credentials:

```powershell
python -m pytest -q
```

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
  - **Cloudflare WARP Transition**: Immediately switches to WARP after OTP submission to bypass corporate firewall restrictions on Telkomsel's Vaadin 7 SPA portal.
  - **Automatic Reauthentication Cycle**: If a session expires during continuous monitoring, WARP is temporarily disconnected, the office route is restored to fetch a new OTP, and WARP is re-engaged.
  - **Safety & Non-Destructive**: Never disconnects pre-existing tunnels or unrelated VPN interfaces (e.g. Tailscale).
- **Vaadin 7 SPA State Machine**:
  - Reliable menu navigation avoiding invalid direct-URL hash changes (`#!dashboard`).
  - DOM stabilization checks, timeout recovery, and automatic page reloads.
- **Security by Design**:
  - Zero plaintext credential logging (uses masked `SecretValue`).
  - Ephemeral public IP and diagnostic inspections.

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
├── docs/                     # Specifications and technical documentation
│   ├── design.md
│   ├── requirements.md
│   ├── tasks.md
│   └── CONTEXT.md
├── logs/                     # Timestamped runtime logs (ignored by git)
└── tests/                    # Offline unit tests & opt-in live workflow tests
```

---

## Prerequisites

1. **Python 3.10+** (tested on Python 3.14 on Windows 64-bit).
2. **Playwright Firefox**:
   ```powershell
   playwright install firefox
   ```
3. **Cloudflare WARP Client**: Installed at `C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe`.
4. *(Optional / Off-office)* **Check Point Endpoint Security**: Installed at `C:\Program Files (x86)\CheckPoint\Endpoint Connect\trac.exe`.

---

## Configuration (`.env`)

Copy `.env.example` to `.env` and fill in your credentials:

```env
CMP_CAS_URL=https://ep.iotcc.telkomsel.com/cas/login?service=https%3A%2F%2Fep.iotcc.telkomsel.com%2Fcas%2Foauth2.0%2FcallbackAuthorize%3Fclient_id%3DenterprisePortal%26redirect_uri%3Dhttps%253A%252F%252Fep.iotcc.telkomsel.com%26response_type%3Dcode%26client_name%3DCasOAuthClient
CMP_PRODUCTS_URL=https://ep.iotcc.telkomsel.com/#!products
CMP_DASHBOARD_URL=https://ep.iotcc.telkomsel.com/#!dashboard

CMP_USERNAME=your_cmp_username
CMP_PASSWORD=your_cmp_password

IMAP_HOST=mail.gmf-aeroasia.co.id
IMAP_PORT=993
IMAP_USERNAME=your_email@gmf-aeroasia.co.id
IMAP_PASSWORD=your_email_password
IMAP_TLS_MODE=imaps
IMAP_VERIFY_TLS=true
IMAP_MAILBOX=INBOX
OTP_SUBJECT=CMP - YOUR TOKEN

HEADLESS=false   # Set to false to see the browser UI live
LOG_LEVEL=INFO

# Check Point Settings
CHECKPOINT_SITE=VPN-GMF
CHECKPOINT_GATEWAY_NAME=GMFINETFW01
CHECKPOINT_AUTH_MODE=credentials
CHECKPOINT_USERNAME=your_checkpoint_user
CHECKPOINT_PASSWORD=your_checkpoint_pass
CHECKPOINT_TRAC_PATH="C:/Program Files (x86)/CheckPoint/Endpoint Connect/trac.exe"

# WARP Settings
WARP_CLI_PATH="C:/Program Files/Cloudflare/Cloudflare WARP/warp-cli.exe"
WARP_MODE=warp
```

---

## Usage

### 1. Run Continuous Dashboard Monitor
```powershell
python main.py
```
To stop the monitor safely, press `Ctrl + C`. All browser instances and VPN tunnels owned by the program will be cleanly restored.

### 2. Run Offline Tests
All 370+ unit tests run fully offline without real network or credential requirements:
```powershell
python -m pytest -q
```

### 3. Run Live Roundtrip Integration Test
Executes one full live sequence (Office/Check Point -> IMAP OTP -> WARP connect -> Dashboard verify -> State restore):
```powershell
python -m pytest tests/test_live_workflow.py::test_live_imap_first_cmp_dashboard_roundtrip -m live -q -s -o log_cli=true -o log_cli_level=INFO
```

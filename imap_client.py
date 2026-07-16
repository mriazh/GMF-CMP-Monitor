"""IMAP adapter for fetching OTP messages from GMF mailbox.

Connects using IMAPS with mandatory TLS verification.
Opens the configured mailbox read-only and polls for fresh OTP emails.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from email.header import decode_header
from typing import Protocol

from config import Settings, EXACT_OTP_SUBJECT
from otp import OtpMessage, extract_otp, find_latest_candidate, OtpError, fetch_and_verify_otp

log = logging.getLogger(__name__)


class Clock(Protocol):
    """Protocol for time-keeping to allow test injection."""
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Real system clock."""
    def now(self) -> float:
        return time.time()
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class OtpProviderProtocol(Protocol):
    """Protocol for OTP providers."""
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def poll_for_otp(self, run_start: datetime) -> str: ...


class ImapConnectionError(Exception):
    """Raised when IMAP connection fails with a safe message."""
    pass


class ImapClient:
    """IMAP adapter for GMF mailbox OTP polling.

    Connects via IMAPS (or STARTTLS), opens the configured mailbox read-only,
    and polls for fresh OTP emails.
    """

    def __init__(
        self,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or SystemClock()
        self._connection: imaplib.IMAP4 | None = None

    def _connect_imap(self) -> imaplib.IMAP4:
        host = self._settings.imap_host
        port = self._settings.imap_port
        username = self._settings.imap_username.get_secret_value()
        password = self._settings.imap_password.get_secret_value()

        log.info("Connecting to IMAP server %s:%s (mode=%s)", host, port, self._settings.imap_tls_mode)

        if self._settings.imap_tls_mode == "imaps":
            conn = imaplib.IMAP4_SSL(host, port)
        else:
            conn = imaplib.IMAP4(host, port)
            conn.starttls()

        log.info("IMAP login (username redacted)")
        conn.login(username, password)
        return conn

    def connect(self) -> None:
        conn = None
        try:
            conn = self._connect_imap()
            mailbox = self._settings.imap_mailbox
            status, _data = conn.select(mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Failed to select mailbox '{mailbox}'")
            self._connection = conn
            log.info("Connected to IMAP mailbox '%s' (read-only)", mailbox)
        except Exception as exc:
            # Clean up partial connection
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    conn.logout()
                except Exception:
                    pass
            self._connection = None
            # Raise safe exception without sensitive data
            raise ImapConnectionError("IMAP connection failed") from exc

    def disconnect(self) -> None:
        if self._connection is not None:
            conn = self._connection
            self._connection = None
            # Attempt close and logout independently
            # Even if close fails, we still try logout
            try:
                log.info("Closing IMAP connection")
                conn.close()
            except Exception:
                log.warning("Error during IMAP close")
            try:
                conn.logout()
            except Exception:
                log.warning("Error during IMAP logout")

    def _decode_subject(self, subject_raw: str | None) -> str:
        if not subject_raw:
            return ""
        decoded_parts = decode_header(subject_raw)
        decoded = ""
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                decoded += part.decode(charset or "utf-8", errors="replace")
            else:
                decoded += part
        # Do NOT strip - preserve exact subject including whitespace
        # CMP - YOUR TOKEN must be exact; CMP - YOUR TOKEN  must be rejected
        return decoded

    def _extract_body(self, msg: email.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        return payload.decode("utf-8", errors="replace")
                    return str(payload)
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode("utf-8", errors="replace")
            return str(payload)
        return ""

    def _parse_imap_message(self, msg_uid: str, fetch_data: tuple) -> OtpMessage | None:
        try:
            _status, data = fetch_data
            if not data:
                return None

            raw = data[0][1] if isinstance(data[0], tuple) else data[0]
            if isinstance(raw, bytes):
                parsed = email.message_from_bytes(raw)
            else:
                parsed = email.message_from_string(raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace"))

            uid = msg_uid.decode() if isinstance(msg_uid, bytes) else str(msg_uid)
            subject = self._decode_subject(parsed.get("Subject"))

            # Parse INTERNALDATE from the FETCH response
            internal_date = self._extract_internal_date(data)
            if internal_date is None:
                log.warning("Message UID %s missing INTERNALDATE, rejecting", uid)
                return None

            body = self._extract_body(parsed)

            return OtpMessage(uid=uid, subject=subject, internal_date=internal_date, body=body)
        except Exception:
            log.warning("Failed to parse IMAP message")
            return None

    def _extract_internal_date(self, fetch_data: tuple) -> datetime | None:
        """Extract INTERNALDATE from IMAP FETCH response.

        The FETCH response format is typically:
        (UID <uid> INTERNALDATE "<date>" BODY[...])

        We need to parse the raw FETCH response to get the INTERNALDATE.
        """
        try:
            _status, data = fetch_data
            if not data:
                return None

            # The first element of data is the FETCH response line
            # It contains metadata like UID, INTERNALDATE, etc.
            fetch_line = data[0]
            if isinstance(fetch_line, tuple):
                fetch_line = fetch_line[0]

            if not isinstance(fetch_line, bytes):
                fetch_line = fetch_line.encode('utf-8')

            # Parse INTERNALDATE from the FETCH response line
            # Example: b'1 (UID 5 INTERNALDATE "01-Jan-2024 12:34:56 +0000" BODY[] {123}'
            match = re.search(rb'INTERNALDATE\s+"([^"]+)"', fetch_line)
            if match:
                date_str = match.group(1).decode('ascii')
                # Parse IMAP date format: "01-Jan-2024 12:34:56 +0000"
                return self._parse_imap_date(date_str)
        except Exception:
            log.warning("Failed to extract INTERNALDATE from FETCH response")
        return None

    def _parse_imap_date(self, date_str: str) -> datetime | None:
        """Parse IMAP INTERNALDATE format: '01-Jan-2024 12:34:56 +0000'"""
        try:
            # IMAP date format: DD-Mon-YYYY HH:MM:SS +ZZZZ
            match = re.match(r'(\d{2})-(\w{3})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+([+-]\d{4})', date_str)
            if not match:
                return None

            day, month_str, year, hour, minute, second, tz = match.groups()
            month_map = {
                'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
            }
            month = month_map.get(month_str)
            if month is None:
                return None

            # Parse timezone offset
            tz_sign = 1 if tz[0] == '+' else -1
            tz_hours = int(tz[1:3])
            tz_minutes = int(tz[3:5])
            tz_offset = tz_sign * (tz_hours * 3600 + tz_minutes * 60)
            tzinfo = timezone(timedelta(seconds=tz_offset))

            return datetime(
                int(year), month, int(day),
                int(hour), int(minute), int(second),
                tzinfo=tzinfo
            )
        except Exception:
            return None

    def _search_uids(self) -> list[bytes]:
        """Search for message UIDs using UID SEARCH."""
        if self._connection is None:
            raise RuntimeError("IMAP connection not established")
        status, msg_uids = self._connection.uid("search", None, "ALL")
        if status != "OK" or not msg_uids or not msg_uids[0]:
            return []
        return msg_uids[0].split()

    def fetch_messages(self) -> list[OtpMessage]:
        """Fetch all messages from the configured mailbox using UID FETCH."""
        if self._connection is None:
            raise RuntimeError("IMAP connection not established")

        msg_uids = self._search_uids()
        messages: list[OtpMessage] = []

        for msg_uid in msg_uids:
            status, data = self._connection.uid("fetch", msg_uid, "(INTERNALDATE BODY.PEEK[])")
            if status != "OK" or not data:
                continue
            parsed = self._parse_imap_message(msg_uid, data)
            if parsed is not None:
                messages.append(parsed)

        return messages

    def recheck(self, uid: str) -> OtpMessage | None:
        """Recheck that a message with the given UID still exists and hasn't changed."""
        if self._connection is None:
            raise RuntimeError("IMAP connection not established")

        try:
            status, data = self._connection.uid("fetch", uid, "(INTERNALDATE BODY.PEEK[])")
            if status != "OK" or not data:
                return None
            return self._parse_imap_message(uid, data)
        except Exception:
            return None

    def poll_for_otp(self, run_start: datetime) -> str:
        """Poll for a fresh valid OTP email within the configured timeout.

        Returns the extracted OTP value.
        Raises OtpError on timeout.
        """
        if self._connection is None:
            raise RuntimeError("IMAP connection not established")

        deadline = self._clock.now() + self._settings.otp_timeout_seconds

        log.info("Polling for OTP (timeout=%.0fs)", self._settings.otp_timeout_seconds)

        while self._clock.now() < deadline:
            messages = self.fetch_messages()

            try:
                otp = fetch_and_verify_otp(
                    messages=messages,
                    run_start=run_start,
                    recheck=self.recheck,
                )
                log.info("OTP extracted successfully")
                return otp
            except OtpError:
                self._clock.sleep(self._settings.otp_poll_interval_seconds)
                continue

        raise OtpError("OTP polling timed out")
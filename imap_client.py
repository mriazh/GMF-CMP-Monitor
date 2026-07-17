"""IMAP adapter for fetching OTP messages from GMF mailbox.

Connects using IMAPS with mandatory TLS verification.
Opens the configured mailbox read-only and polls for fresh OTP emails.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from email.header import decode_header
from typing import Protocol

from config import Settings
from otp import OtpMessage, OtpError, fetch_and_verify_otp

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
    """Protocol for OTP providers hooking into IMAP."""
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def poll_for_otp(self, run_start: datetime) -> str: ...


class ImapConnectionError(Exception):
    """Raised when IMAP connection fails with a safe message."""


class ImapClient:
    """IMAP adapter for GMF mailbox OTP polling."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or SystemClock()
        self._connection: imaplib.IMAP4 | None = None
        self._base_uid: int = 0

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
            # Take UID snapshot at initial connect time (before authentication) to avoid
            # race condition where a previous run's delayed OTP email arrives
            # during the current run's poll.
            # On reconnect, reuse the original UID baseline.
            if self._base_uid == 0:
                self._take_uid_snapshot()
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

    def _take_uid_snapshot(self) -> None:
        """Take a snapshot of the highest UID matching OTP criteria.

        Called at connect time (before authentication) to establish a baseline
        so that only newly arriving OTP emails (higher UID) are accepted.
        """
        try:
            # Use current time as run_start reference; since_date covers 1 day
            # which is wider than the poll's window but safe for snapshotting.
            since_date = self._since_cutoff_date(
                datetime.fromtimestamp(self._clock.now(), tz=timezone.utc)
            )
            max_uid = 0
            for uid in self._search_uids(since_date):
                uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
                try:
                    max_uid = max(max_uid, int(uid_str))
                except (TypeError, ValueError):
                    continue
            self._base_uid = max_uid
            log.debug("IMAP UID snapshot at connect: %s", self._base_uid)
        except Exception as exc:
            log.warning("Failed to take UID snapshot: %s", exc)
            self._base_uid = 0

    def disconnect(self) -> None:
        if self._connection is not None:
            conn = self._connection
            self._connection = None
            # Attempt close and logout independently
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
        return decoded

    def _extract_body(self, msg: email.message.Message) -> str:
        plain_body = ""
        html_body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" and not plain_body:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        plain_body = payload.decode("utf-8", errors="replace")
                    else:
                        plain_body = str(payload)
                elif content_type == "text/html" and not html_body:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        html_body = payload.decode("utf-8", errors="replace")
                    else:
                        html_body = str(payload)
            return plain_body if plain_body else html_body
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode("utf-8", errors="replace")
            return str(payload)
        return ""

    def _parse_imap_message(self, msg_uid: str, fetch_data: tuple) -> OtpMessage | None:
        try:
            items = fetch_data[1] if (isinstance(fetch_data, tuple) and len(fetch_data) == 2 and isinstance(fetch_data[0], str)) else fetch_data
            if not items or not isinstance(items, (list, tuple)):
                return None

            raw_bytes = None
            for item in items:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw_bytes = item[1]
                    break

            if not raw_bytes or not isinstance(raw_bytes, bytes):
                return None

            parsed = email.message_from_bytes(raw_bytes)
            uid = msg_uid.decode() if isinstance(msg_uid, bytes) else str(msg_uid)
            subject = self._decode_subject(parsed.get("Subject"))

            internal_date = self._extract_internal_date(items)
            if internal_date is None:
                date_header = parsed.get("Date")
                if date_header:
                    try:
                        internal_date = email.utils.parsedate_to_datetime(date_header)
                    except Exception:
                        internal_date = None

            if internal_date is None:
                log.warning("Message UID %s missing/invalid INTERNALDATE and Date, rejecting", uid)
                return None

            body = self._extract_body(parsed)
            return OtpMessage(uid=uid, subject=subject, internal_date=internal_date, body=body)
        except Exception as exc:
            log.warning("Failed to parse IMAP message UID %s: %s", msg_uid, exc)
            return None

    def _extract_internal_date(self, fetch_data: tuple) -> datetime | None:
        items = fetch_data[1] if (isinstance(fetch_data, tuple) and len(fetch_data) == 2 and isinstance(fetch_data[0], str)) else fetch_data
        if not isinstance(items, (list, tuple)):
            return None
        for item in items:
            if isinstance(item, tuple) and len(item) >= 1:
                header_line = item[0]
                if not isinstance(header_line, bytes):
                    header_line = str(header_line).encode("utf-8")
                match = re.search(rb'INTERNALDATE\s+"([^"]+)"', header_line, re.IGNORECASE)
                if match:
                    date_str = match.group(1).decode("ascii", errors="replace")
                    return self._parse_imap_date(date_str)
        return None

    def _parse_imap_date(self, date_str: str) -> datetime | None:
        try:
            match = re.match(r'\s*(\d{1,2})-(\w{3})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+([+-]\d{4})', date_str)
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

            tz_sign = 1 if tz[0] == '+' else -1
            tz_hours = int(tz[1:3])
            tz_minutes = int(tz[3:5])
            tz_offset = tz_sign * (tz_hours * 3600 + tz_minutes * 60)
            tzinfo = timezone(timedelta(seconds=tz_offset))
            
            return datetime(int(year), month, int(day), int(hour), int(minute), int(second), tzinfo=tzinfo)
        except Exception:
            return None

    def _search_uids(self, since_date: str | None = None) -> list[bytes]:
        """Search for candidate UIDs.

        When ``since_date`` (IMAP ``SINCE`` date, e.g. ``6-Aug-2026``) is given,
        restrict the search to messages from that date with the configured OTP
        subject. Otherwise fall back to searching the whole mailbox (used by
        callers that only need the raw UID list).

        Fetching every message in a large mailbox is very slow (the full body of
        every message is transferred), so the OTP poll must always narrow the
        search instead of using ``ALL``.
        """
        if not self._connection:
            return []
        if since_date:
            criteria = (
                f'(SINCE "{since_date}" HEADER Subject '
                f'"{self._settings.otp_subject}")'
            )
        else:
            criteria = "ALL"
        status, data = self._connection.uid("search", None, criteria)
        if status != "OK":
            return []
        return data[0].split()

    def _fetch_message(self, uid: str) -> tuple | None:
        if not self._connection:
            return None
        status, data = self._connection.uid("fetch", uid, "(INTERNALDATE BODY.PEEK[])")
        if status != "OK":
            return None
        return data

    def _refresh_mailbox(self) -> None:
        """Refresh the mailbox view with NOOP so new emails are visible.

        Falls back to re-selecting the configured mailbox read-only if the
        NOOP fails (e.g. stale connection).
        """
        if self._connection is None:
            raise RuntimeError("IMAP connection not established")
        try:
            self._connection.noop()
        except Exception:
            log.warning("NOOP failed, re-selecting mailbox")
            status, _ = self._connection.select(self._settings.imap_mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError("Failed to re-select mailbox")

    def _since_cutoff_date(self, run_start: datetime) -> str:
        """IMAP SINCE date covering the acceptance window.

        IMAP ``SINCE`` compares against the server's internal date (usually UTC),
        so subtract a full day from the login timestamp to absorb timezone and
        server-clock differences while keeping the candidate set small.
        """
        cutoff = run_start - timedelta(days=1)
        return f"{cutoff.day}-{cutoff.strftime('%b')}-{cutoff.year}"

    def poll_for_otp(self, run_start: datetime) -> str:
        if not self._connection:
            raise RuntimeError("IMAP connection not established")

        deadline = self._clock.now() + self._settings.otp_timeout_seconds
        since_date = self._since_cutoff_date(run_start)

        # Use the UID snapshot taken at connect time (before authentication).
        # Fall back to taking a fresh snapshot if not available.
        max_uid_at_start = self._base_uid
        if max_uid_at_start == 0:
            try:
                for uid in self._search_uids(since_date):
                    uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
                    try:
                        max_uid_at_start = max(max_uid_at_start, int(uid_str))
                    except (TypeError, ValueError):
                        continue
            except Exception:
                max_uid_at_start = 0
        log.debug("Polling for OTP with base UID %s", max_uid_at_start)

        log.info(
            "Polling for OTP via IMAP (timeout=%ds, since=%s).",
            self._settings.otp_timeout_seconds,
            since_date,
        )

        while self._clock.now() < deadline:
            try:
                self._refresh_mailbox()
                uids = self._search_uids(since_date)
                messages: list[OtpMessage] = []
                for uid in uids:
                    uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
                    try:
                        if int(uid_str) <= max_uid_at_start:
                            # Leftover message from a previous login attempt.
                            continue
                    except (TypeError, ValueError):
                        pass
                    fetch_data = self._fetch_message(uid_str)
                    if fetch_data:
                        msg = self._parse_imap_message(uid, fetch_data)
                        if msg:
                            messages.append(msg)

                if messages:
                    newest = max(m.internal_date for m in messages)
                    log.debug(
                        "Poll iteration: %d candidate(s), newest date=%s",
                        len(messages),
                        newest.isoformat(),
                    )

                otp = fetch_and_verify_otp(
                    messages,
                    run_start,
                    self._recheck_uid,
                    self._settings.otp_clock_skew_tolerance_seconds
                )
                log.info("OTP retrieved successfully")
                return otp
            except OtpError:
                self._clock.sleep(self._settings.otp_poll_interval_seconds)
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as exc:
                log.warning(
                    "IMAP connection error during polling: %s, reconnecting.",
                    type(exc).__name__,
                )
                try:
                    self.disconnect()
                    self.connect()
                except Exception:
                    log.warning("IMAP reconnect failed, will retry")
                self._clock.sleep(self._settings.otp_poll_interval_seconds)

        raise OtpError("OTP polling timed out")

    def _recheck_uid(self, uid: str) -> OtpMessage | None:
        fetch_data = self._fetch_message(uid)
        if fetch_data:
            return self._parse_imap_message(uid.encode(), fetch_data)
        return None

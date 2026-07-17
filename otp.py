from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import re
from typing import Callable, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OtpMessage:
    uid: str
    subject: str
    internal_date: datetime
    body: str

    def __repr__(self) -> str:
        return f"OtpMessage(uid={self.uid!r}, subject={self.subject!r}, internal_date={self.internal_date!r}, body=<redacted>)"


class OtpError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self._message = message

    def __str__(self) -> str:
        return self._message


def extract_otp(body: str) -> str:
    token_match = re.search(r"token[^\d]*(\d{6})", body, re.IGNORECASE)
    if token_match:
        return token_match.group(1)

    matches = re.findall(r"\b\d{6}\b", body)
    if len(matches) == 1:
        return matches[0]

    raise OtpError("OTP extraction failed")


def validate_subject(subject: str) -> None:
    if subject != "CMP - YOUR TOKEN":
        raise OtpError("Invalid subject")


def validate_internal_date(
    internal_date: datetime, run_start: datetime, tolerance_seconds: int = 120
) -> None:
    if internal_date.tzinfo is None:
        raise OtpError("Naive timestamp")
    cutoff = run_start - timedelta(seconds=tolerance_seconds)
    if internal_date < cutoff:
        raise OtpError(
            f"Stale message: date {internal_date.isoformat()} is before cutoff {cutoff.isoformat()}"
        )


def find_latest_candidate(
    messages: list[OtpMessage], run_start: datetime, tolerance_seconds: int = 120
) -> OtpMessage:
    valid = []
    for msg in messages:
        try:
            validate_subject(msg.subject)
            validate_internal_date(msg.internal_date, run_start, tolerance_seconds)
            log.info(
                "Found valid OTP candidate email UID %s (date=%s)",
                msg.uid,
                msg.internal_date.isoformat(),
            )
            valid.append(msg)
        except OtpError as exc:
            log.debug("Candidate email UID %s rejected: %s", msg.uid, exc)
            continue
    if not valid:
        raise OtpError("No valid candidate")
    return max(valid, key=lambda m: m.internal_date)


def fetch_and_verify_otp(
    messages: list[OtpMessage],
    run_start: datetime,
    recheck: Callable[[str], Optional[OtpMessage]],
    tolerance_seconds: int = 120,
) -> str:
    candidate = find_latest_candidate(messages, run_start, tolerance_seconds)
    rechecked = recheck(candidate.uid)
    if rechecked is None:
        raise OtpError("Candidate vanished on recheck")
    if rechecked.uid != candidate.uid:
        raise OtpError("Candidate changed on recheck")
    if rechecked.subject != candidate.subject:
        raise OtpError("Candidate changed on recheck")
    if rechecked.internal_date != candidate.internal_date:
        raise OtpError("Candidate changed on recheck")
    if rechecked.body != candidate.body:
        raise OtpError("Candidate changed on recheck")
    return extract_otp(rechecked.body)
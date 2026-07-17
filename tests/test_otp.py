import pytest
from datetime import datetime, timezone, timedelta
from otp import (
    OtpMessage,
    OtpError,
    extract_otp,
    validate_subject,
    validate_internal_date,
    find_latest_candidate,
    fetch_and_verify_otp,
)


class TestExtractOtp:
    def test_single_valid_otp(self):
        body = "Your OTP is 123456 for verification"
        assert extract_otp(body) == "123456"

    def test_no_otp_raises(self):
        body = "No code here"
        with pytest.raises(OtpError, match="OTP extraction failed"):
            extract_otp(body)

    def test_multiple_otps_raises(self):
        body = "Codes: 123456 and 654321"
        with pytest.raises(OtpError, match="OTP extraction failed"):
            extract_otp(body)

    def test_malformed_otp_too_short(self):
        body = "Code: 12345"
        with pytest.raises(OtpError, match="OTP extraction failed"):
            extract_otp(body)

    def test_otp_after_token_word(self):
        body = "Your one-time token for accessing the Telkomsel IoT Portal is: 050909"
        assert extract_otp(body) == "050909"

    def test_otp_in_html_body(self):
        body = '<p>Your one-time token for accessing the Telkomsel IoT Portal is: <strong>050909</strong></p>'
        assert extract_otp(body) == "050909"


class TestValidateSubject:
    def test_exact_subject_passes(self):
        validate_subject("CMP - YOUR TOKEN")

    def test_near_match_fails(self):
        with pytest.raises(OtpError, match="Invalid subject"):
            validate_subject("CMP - YOUR TOKEN ")

    def test_wrong_subject_fails(self):
        with pytest.raises(OtpError, match="Invalid subject"):
            validate_subject("CMP - YOUR CODE")


class TestValidateInternalDate:
    def test_aware_timestamp_after_run_start_passes(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        internal_date = datetime(2024, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
        validate_internal_date(internal_date, run_start)

    def test_tolerance_grace_period_passes(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Message 1 minute before run_start
        internal_date = run_start - timedelta(minutes=1)
        validate_internal_date(internal_date, run_start, tolerance_seconds=120)

    def test_outside_tolerance_fails(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Message 3 minutes before run_start (outside 120s tolerance)
        internal_date = run_start - timedelta(minutes=3)
        with pytest.raises(OtpError, match="Stale message"):
            validate_internal_date(internal_date, run_start, tolerance_seconds=120)

    def test_stale_message_raises(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        internal_date = datetime(2024, 1, 1, 11, 59, 59, tzinfo=timezone.utc)
        # tolerance_seconds=0 preserves the strict pre-tolerance behavior:
        # any message dated before run_start is stale.
        with pytest.raises(OtpError, match="Stale message"):
            validate_internal_date(internal_date, run_start, tolerance_seconds=0)

    def test_different_timezone_handled(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        internal_date = datetime(2024, 1, 1, 14, 0, 5, tzinfo=timezone(timedelta(hours=2)))
        validate_internal_date(internal_date, run_start)


class TestFindLatestCandidate:
    def test_selects_newest_valid_message(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg1 = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 111111")
        msg2 = OtpMessage("2", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 3, tzinfo=timezone.utc), "Code: 222222")
        msg3 = OtpMessage("3", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 2, tzinfo=timezone.utc), "Code: 333333")
        result = find_latest_candidate([msg1, msg2, msg3], run_start)
        assert result.uid == "2"

    def test_ignores_invalid_subject(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg1 = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 111111")
        msg2 = OtpMessage("2", "CMP - YOUR CODE", datetime(2024, 1, 1, 12, 0, 3, tzinfo=timezone.utc), "Code: 222222")
        result = find_latest_candidate([msg1, msg2], run_start)
        assert result.uid == "1"

    def test_ignores_stale_message(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg1 = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 111111")
        msg2 = OtpMessage("2", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 11, 59, 59, tzinfo=timezone.utc), "Code: 222222")
        result = find_latest_candidate([msg1, msg2], run_start)
        assert result.uid == "1"

    def test_no_valid_candidate_raises(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg1 = OtpMessage("1", "WRONG", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 111111")
        with pytest.raises(OtpError, match="No valid candidate"):
            find_latest_candidate([msg1], run_start)


class TestFetchAndVerifyOtp:
    def test_successful_flow(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 123456")
        messages = [msg]

        def recheck(uid):
            return msg

        otp = fetch_and_verify_otp(messages, run_start, recheck)
        assert otp == "123456"

    def test_candidate_vanished_on_recheck_raises(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 123456")

        def recheck(uid):
            return None

        with pytest.raises(OtpError, match="Candidate vanished on recheck"):
            fetch_and_verify_otp([msg], run_start, recheck)

    def test_candidate_changed_on_recheck_raises(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg1 = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 123456")
        msg2 = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 2, tzinfo=timezone.utc), "Code: 654321")

        def recheck(uid):
            return msg2

        with pytest.raises(OtpError, match="Candidate changed on recheck"):
            fetch_and_verify_otp([msg1], run_start, recheck)

    def test_otp_extracted_from_rechecked_body(self):
        """OTP must come from the rechecked body, never the original candidate."""
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        candidate = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 111111")
        # Recheck must return an identical message; OTP is extracted from the
        # rechecked body, not the original candidate body.
        rechecked = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 111111")

        def recheck(uid):
            assert uid == "1"
            return rechecked

        otp = fetch_and_verify_otp([candidate], run_start, recheck)
        assert otp == "111111"


class TestImmutabilityAndRedaction:
    def test_otp_message_is_immutable(self):
        msg = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 123456")
        with pytest.raises(AttributeError):
            msg.uid = "2"

    def test_repr_redacts_body(self):
        msg = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 123456")
        repr_str = repr(msg)
        assert "123456" not in repr_str
        assert "<redacted>" in repr_str

    def test_exception_does_not_leak_otp(self):
        with pytest.raises(OtpError) as exc_info:
            extract_otp("Codes: 123456 and 654321")
        assert "123456" not in str(exc_info.value)
        assert "654321" not in str(exc_info.value)

    def test_no_mutation_of_input_list(self):
        run_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg = OtpMessage("1", "CMP - YOUR TOKEN", datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc), "Code: 123456")
        messages = [msg]
        original_len = len(messages)

        def recheck(uid):
            return msg

        fetch_and_verify_otp(messages, run_start, recheck)
        assert len(messages) == original_len
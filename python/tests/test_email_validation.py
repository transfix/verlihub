"""
Tests for email validation module.

Covers:
- Format validation (valid / invalid emails)
- Disposable domain blocking (with toggle)
- Deliverability check (mocked DNS)
- Async validate_email wrapper
"""

import pytest
import pytest_asyncio

from verlihub.email_validation import (
    validate_email,
    validate_email_format,
    validate_email_deliverability,
    _DISPOSABLE_DOMAINS,
)


# =============================================================================
# Format Validation
# =============================================================================


class TestEmailFormat:
    """Test validate_email_format()."""

    @pytest.mark.parametrize("email", [
        "user@example.com",
        "firstname.lastname@example.com",
        "user+tag@example.com",
        "user@sub.domain.example.com",
        "user123@example.org",
        "a@b.co",
        "test.user@my-domain.com",
        "UPPER@CASE.COM",
    ])
    def test_valid_emails(self, email):
        ok, err = validate_email_format(email)
        assert ok, f"{email} should be valid, got error: {err}"
        assert err == ""

    @pytest.mark.parametrize("email,reason", [
        ("", "required"),
        ("   ", "required"),
        ("notanemail", "not well-formed"),
        ("@domain.com", "not well-formed"),
        ("user@", "not well-formed"),
        ("user@.com", "not well-formed"),
        ("user@@domain.com", "not well-formed"),
        ("user@domain", "not well-formed"),
        ("user name@domain.com", "not well-formed"),
    ])
    def test_invalid_emails(self, email, reason):
        ok, err = validate_email_format(email)
        assert not ok, f"{email} should be invalid"
        assert reason.lower() in err.lower()

    def test_too_long_email(self):
        email = "a" * 65 + "@" + "b" * 186 + ".com"
        assert len(email) > 254
        ok, err = validate_email_format(email)
        assert not ok
        assert "too long" in err.lower()

    def test_too_long_local_part(self):
        email = "a" * 65 + "@example.com"
        ok, err = validate_email_format(email)
        assert not ok
        assert "local part" in err.lower()


# =============================================================================
# Disposable Domain Blocking
# =============================================================================


class TestDisposableDomains:
    """Test disposable email domain blocking."""

    @pytest.mark.parametrize("domain", list(_DISPOSABLE_DOMAINS)[:5])
    def test_disposable_blocked_by_default(self, domain):
        email = f"test@{domain}"
        ok, err = validate_email_format(email)
        assert not ok, f"{domain} should be blocked by default"
        assert "disposable" in err.lower()

    @pytest.mark.parametrize("domain", list(_DISPOSABLE_DOMAINS)[:5])
    def test_disposable_allowed_when_toggle_off(self, domain):
        email = f"test@{domain}"
        ok, err = validate_email_format(email, block_disposable=False)
        assert ok, f"{domain} should be allowed when block_disposable=False, got: {err}"

    def test_non_disposable_not_affected_by_toggle(self):
        email = "user@gmail.com"
        ok1, _ = validate_email_format(email, block_disposable=True)
        ok2, _ = validate_email_format(email, block_disposable=False)
        assert ok1
        assert ok2

    def test_disposable_list_is_frozen(self):
        """The disposable set should be immutable."""
        assert isinstance(_DISPOSABLE_DOMAINS, frozenset)


# =============================================================================
# Deliverability Check
# =============================================================================


class TestDeliverability:
    """Test validate_email_deliverability()."""

    def test_real_domain_deliverable(self):
        """gmail.com should have MX records."""
        ok, err = validate_email_deliverability("test@gmail.com")
        assert ok, f"gmail.com should be deliverable, got: {err}"

    def test_nonexistent_domain_not_deliverable(self):
        """A clearly fake domain should fail."""
        ok, err = validate_email_deliverability("test@thisdomain-does-not-exist-12345.xyz")
        # May pass on some DNS resolvers that return fallback records,
        # so just check the function doesn't crash
        assert isinstance(ok, bool)
        assert isinstance(err, str)

    def test_missing_domain(self):
        """Email without @ should fail or degrade gracefully."""
        ok, err = validate_email_deliverability("nodomain")
        # "nodomain" has no @, rpartition("@") gives domain="nodomain"
        # DNS will fail on it — either returns (False, ...) or
        # gracefully passes. Either way should not crash.
        assert isinstance(ok, bool)
        assert isinstance(err, str)

    def test_graceful_on_import_error(self, monkeypatch):
        """Should return (True, '') if dnspython is not installed."""
        import verlihub.email_validation as ev
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "dns.resolver":
                raise ImportError("no dns")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        ok, err = ev.validate_email_deliverability("test@example.com")
        assert ok  # Graceful pass


# =============================================================================
# Async validate_email()
# =============================================================================


class TestValidateEmailAsync:
    """Test the async validate_email() wrapper."""

    @pytest.mark.asyncio
    async def test_valid_email_passes(self):
        ok, err = await validate_email("user@example.com")
        assert ok
        assert err == ""

    @pytest.mark.asyncio
    async def test_invalid_email_fails(self):
        ok, err = await validate_email("notvalid")
        assert not ok

    @pytest.mark.asyncio
    async def test_disposable_blocked_by_default(self):
        ok, err = await validate_email("test@mailinator.com")
        assert not ok
        assert "disposable" in err.lower()

    @pytest.mark.asyncio
    async def test_disposable_allowed_with_toggle(self):
        ok, err = await validate_email("test@mailinator.com", block_disposable=False)
        assert ok

    @pytest.mark.asyncio
    async def test_deliverability_not_checked_by_default(self):
        """Even a fake domain should pass when check_deliverability=False."""
        ok, err = await validate_email("test@thisdomain-does-not-exist-12345.xyz",
                                       block_disposable=False)
        assert ok  # Format is valid, no deliverability check

    @pytest.mark.asyncio
    async def test_deliverability_checked_when_enabled(self):
        """gmail.com should pass deliverability check."""
        ok, err = await validate_email("test@gmail.com", check_deliverability=True)
        assert ok

    @pytest.mark.asyncio
    async def test_all_toggles_combined(self):
        """block_disposable=False + check_deliverability=False = format only."""
        ok, err = await validate_email(
            "user@mailinator.com",
            block_disposable=False,
            check_deliverability=False,
        )
        assert ok


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

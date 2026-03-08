"""
Email validation utilities for Verlihub registration.

Provides:
- Format validation (RFC 5322 compliant)
- DNS MX record check (deliverability)
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Comprehensive email regex — covers the vast majority of valid addresses
# without being overly permissive.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Known disposable/temporary email domains (subset — extend as needed)
_DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "guerrillamail.com", "tempmail.com",
    "throwaway.email", "yopmail.com", "sharklasers.com",
    "guerrillamailblock.com", "grr.la", "dispostable.com",
    "temp-mail.org", "trashmail.com", "10minutemail.com",
})


def validate_email_format(email: str) -> tuple[bool, str]:
    """Validate email format.

    Returns:
        (is_valid, error_message)
    """
    if not email or not email.strip():
        return False, "Email address is required"

    email = email.strip().lower()

    if len(email) > 254:
        return False, "Email address is too long"

    if not _EMAIL_RE.match(email):
        return False, "Email address is not well-formed"

    local, _, domain = email.rpartition("@")
    if not local or not domain:
        return False, "Email address is not well-formed"

    if len(local) > 64:
        return False, "Email local part is too long"

    if domain in _DISPOSABLE_DOMAINS:
        return False, "Disposable email addresses are not allowed"

    return True, ""


def validate_email_deliverability(email: str) -> tuple[bool, str]:
    """Check if the email domain has valid MX records.

    This is a blocking DNS lookup — should be called from an async
    context via ``run_in_executor`` or from a background thread.

    Returns:
        (is_deliverable, error_message)
    """
    _, _, domain = email.strip().lower().rpartition("@")
    if not domain:
        return False, "Invalid email domain"

    try:
        import dns.resolver

        try:
            answers = dns.resolver.resolve(domain, "MX")
            if answers:
                return True, ""
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            pass
        except dns.resolver.NoNameservers:
            pass

        # Fallback: check for A/AAAA record (some domains accept mail
        # without an explicit MX record).
        try:
            answers = dns.resolver.resolve(domain, "A")
            if answers:
                return True, ""
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            pass
        except dns.resolver.NoNameservers:
            pass

        return False, f"The domain '{domain}' does not appear to accept email"

    except ImportError:
        logger.warning(
            "dnspython not installed — skipping email deliverability check. "
            "Install with: pip install dnspython"
        )
        # Gracefully pass if the library isn't installed
        return True, ""
    except Exception as exc:
        logger.warning("Email deliverability check failed for %s: %s", domain, exc)
        # Don't block registration on unexpected DNS errors
        return True, ""


async def validate_email(
    email: str,
    *,
    check_deliverability: bool = False,
) -> tuple[bool, str]:
    """Full email validation (async-safe).

    Args:
        email: The email address to validate.
        check_deliverability: If True, also check DNS MX records.

    Returns:
        (is_valid, error_message)
    """
    ok, err = validate_email_format(email)
    if not ok:
        return False, err

    if check_deliverability:
        import asyncio
        loop = asyncio.get_running_loop()
        ok, err = await loop.run_in_executor(
            None, validate_email_deliverability, email
        )
        if not ok:
            return False, err

    return True, ""

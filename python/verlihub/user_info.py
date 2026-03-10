"""
Send "Your information" PM to users on connect.

Replicates the legacy ``cUser::DisplayInfo()`` behaviour controlled by the
``send_user_info`` configuration flag.  When a user completes login (the
``user_connect`` event fires *after* MyINFO + GeoIP), this handler formats
a private message containing the user's nick, IP, country, city, TLS and
NAT status and delivers it from the hub security bot.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verlihub.core import HubContext

logger = logging.getLogger(__name__)

# C++ status flag constants (from src/core/nmdc_protocol.h)
STATUS_TLS = 0x10
STATUS_NAT = 0x20


def _format_info(info: dict) -> str:
    """Build the multi-line info text matching the legacy format."""
    lines: list[str] = ["Your information:"]

    lines.append(f" [*] Nick: {info['nick']}")
    lines.append(f" [*] IP: {info.get('ip', '?')}")

    cc = info.get("country", "")
    cn = info.get("country_name", "")
    if cc and cc != "--":
        lines.append(f" [*] Country: {cc}={cn}")

    city = info.get("city", "")
    if city and city != "--":
        lines.append(f" [*] City: {city}")

    flag = info.get("status_flag", 0)
    client_tls = "Yes" if (flag & STATUS_TLS) else "No"
    client_nat = "Yes" if (flag & STATUS_NAT) else "No"

    # Hub TLS is not yet implemented in the new core — always "No"
    lines.append(" [*] Hub TLS: No")
    lines.append(f" [*] Client TLS: {client_tls}")
    lines.append(f" [*] Client NAT: {client_nat}")

    return "\r\n".join(lines)


def on_user_connect(ctx: "HubContext", nick: str, ip: str) -> None:
    """``user_connect`` event handler — sends the info PM."""
    try:
        # Check the send_user_info config (default "1" = enabled)
        enabled = ctx.get_config("config", "send_user_info", "1")
        if enabled in ("0", "false", "no"):
            return

        info = ctx.get_user_info(nick)
        if info is None:
            return

        bot_nick = ctx.get_config("config", "hub_security", "Hub-Security")
        message = _format_info(info)
        ctx.send_pm_as(bot_nick, nick, message)
    except Exception:
        logger.debug("Failed to send user info to %s", nick, exc_info=True)


def register(ctx: "HubContext") -> None:
    """Register the user-info handler on the hub event bus."""
    # Wrap closure so the handler signature matches (nick, ip)
    def _handler(nick: str, ip: str) -> None:
        on_user_connect(ctx, nick, ip)

    ctx.events.register("user_connect", _handler)
    logger.info("User-info on-connect handler registered")

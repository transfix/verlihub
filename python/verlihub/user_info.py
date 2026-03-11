"""
Send "Your information" to users on connect.

Replicates the legacy ``cUser::DisplayInfo()`` behaviour controlled by the
``send_user_info`` configuration flag.  When a user completes login (the
``user_connect`` event fires *after* MyINFO + GeoIP), this handler formats
a message containing the user's nick, IP, country, city, TLS and NAT status
and delivers it from the hub security bot.

By default the message appears in the user's main chat window.  Set the
``user_info_as_pm`` config key to ``true`` / ``1`` to deliver it as a
private message instead.
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


def _is_truthy(value: str) -> bool:
    """Return ``True`` for values that should be considered 'enabled'."""
    return value.lower() not in ("0", "false", "no", "")


def on_user_connect(ctx: "HubContext", nick: str, ip: str) -> None:
    """``user_connect`` event handler — sends user info on login.

    Delivery mode is controlled by the ``user_info_as_pm`` config key:

    * ``"0"`` / ``"false"`` / ``"no"`` (default) — main-chat message
      visible only to the connecting user.
    * ``"1"`` / ``"true"`` / ``"yes"`` — private message from the
      hub-security bot.
    """
    try:
        # Check the send_user_info config (default "1" = enabled)
        enabled = ctx.get_config("config", "send_user_info", "1")
        if not _is_truthy(enabled):
            return

        info = ctx.get_user_info(nick)
        if info is None:
            logger.warning("get_user_info(%s) returned None", nick)
            return

        bot_nick = ctx.get_config("config", "hub_security", "Hub-Security")
        message = _format_info(info)

        # Deliver as PM or as a main-chat message to the user
        as_pm = ctx.get_config("config", "user_info_as_pm", "0")
        if _is_truthy(as_pm):
            ctx.send_pm_as(bot_nick, nick, message)
        else:
            # Send a chat-style line visible only to this user
            ctx.send_to_user(nick, f"<{bot_nick}> {message}")
    except Exception:
        logger.warning("Failed to send user info to %s", nick, exc_info=True)


def register(ctx: "HubContext") -> None:
    """Register the user-info handler on the hub event bus."""
    # Wrap closure so the handler signature matches (nick, ip)
    def _handler(nick: str, ip: str) -> None:
        on_user_connect(ctx, nick, ip)

    ctx.events.register("user_connect", _handler)
    logger.info("User-info on-connect handler registered")

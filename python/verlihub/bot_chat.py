"""
NMDC Bot Chat — LLM-powered hub security bot.

When users send a private message to the hub security bot (``Hub-Security``),
this module intercepts it via the C++ event callback and forwards the message
to the LLM chat pipeline.  The bot's reply is sent back as an NMDC PM.

Users can also address the bot in main chat (e.g. ``Hub-Security: hello``).
Main-chat interactions always operate at the **lowest** security level — no
tools, purely conversational.

Private messages respect the sender's actual user class:
  * class ≥ ``admin_class`` → admin tools (kick/ban/config)
  * class ≥ ``min_class``   → read-only hub tools
  * lower classes           → conversational only (no tools)

Threading model
~~~~~~~~~~~~~~~
C++ calls ``OnPrivateMessage`` / ``OnChatMessage`` synchronously from the
I/O thread.  We use ``asyncio.run_coroutine_threadsafe()`` to dispatch
the LLM call on the FastAPI event loop, then fire-and-forget the response
(the C++ callback must return quickly).
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from verlihub.core import HubContext, HubEventHandler

log = logging.getLogger("verlihub.bot_chat")

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_BOT_ADMIN = """\
You are {bot_nick}, the security bot of the "{hub_name}" DC++ hub.  You are \
chatting privately with {user_nick} (class {user_class}), who has administrator \
privileges.

You have access to tools that query and control the live hub. Use them to \
answer questions accurately — never guess hub state, always check via tools.

Available capabilities:
- Query online users, operators, bots
- View hub statistics, geographic distribution, share statistics
- Look up individual user details (including IP addresses)
- Kick users, send broadcasts, send private messages
- Execute hub console commands (!help for list)
- Read and write hub configuration

Guidelines:
- Keep responses concise — this is an NMDC chat, not a web page
- Always call tools rather than assuming hub state
- Be direct and professional
- Format numbers readably (e.g. "1.23 TiB" not raw bytes)
"""

SYSTEM_PROMPT_BOT_USER = """\
You are {bot_nick}, the security bot of the "{hub_name}" DC++ hub.  You are \
chatting privately with {user_nick} (class {user_class}).

You have access to read-only tools that show public hub information.  Use \
them to answer questions accurately.

You CANNOT: see IP addresses, kick users, ban users, change configuration, \
execute console commands, or send messages on behalf of users.

Guidelines:
- Keep responses concise — this is an NMDC chat, not a web page
- Always call tools rather than guessing
- Be friendly and helpful
"""

SYSTEM_PROMPT_BOT_PUBLIC = """\
You are {bot_nick}, the security bot of the "{hub_name}" DC++ hub.  A user \
named {user_nick} is talking to you in the main chat.  All users can see \
this conversation.

You have NO tools and NO access to hub internals.  You can only hold a \
friendly, general-purpose conversation.

Guidelines:
- Keep responses short and friendly — this is a public chat room
- Do NOT make up information about the hub
- If asked about hub status, suggest the user PM you for detailed info
- Never reveal private information about users
"""


# ---------------------------------------------------------------------------
# Bot ChatSession (re-uses LLM client & tool definitions from llm.py)
# ---------------------------------------------------------------------------

class BotChatSession:
    """Conversation state for one user ↔ bot session (PM or main-chat)."""

    def __init__(
        self,
        nick: str,
        user_class: int,
        bot_nick: str,
        hub_name: str,
        *,
        mode: str = "pm",  # "pm" or "chat"
        llm_cfg: Any = None,
    ):
        self.nick = nick
        self.user_class = user_class
        self.bot_nick = bot_nick
        self.hub_name = hub_name
        self.mode = mode
        self.llm_cfg = llm_cfg
        self.created_at = time.time()
        self.messages: list[dict] = []
        self.tools: list[dict] = []

        self._build(nick, user_class, bot_nick, hub_name, mode, llm_cfg)

    # -- internal ---------------------------------------------------------

    def _build(
        self,
        nick: str,
        user_class: int,
        bot_nick: str,
        hub_name: str,
        mode: str,
        llm_cfg: Any,
    ) -> None:
        """Select the system prompt and tool set based on context."""
        from verlihub.api.routes.llm import _build_admin_tools, _build_readonly_tools

        fmt = dict(
            bot_nick=bot_nick,
            hub_name=hub_name,
            user_nick=nick,
            user_class=user_class,
        )

        if mode == "chat":
            # Main chat → no tools, lowest security
            self.tools = []
            prompt = SYSTEM_PROMPT_BOT_PUBLIC.format(**fmt)
        else:
            # PM — tools depend on user class
            admin_class = llm_cfg.admin_class if llm_cfg else 5
            min_class = llm_cfg.min_class if llm_cfg else 3

            if user_class >= admin_class:
                self.tools = _build_readonly_tools() + _build_admin_tools()
                prompt = SYSTEM_PROMPT_BOT_ADMIN.format(**fmt)
            elif user_class >= min_class:
                self.tools = _build_readonly_tools()
                prompt = SYSTEM_PROMPT_BOT_USER.format(**fmt)
            else:
                self.tools = []
                prompt = SYSTEM_PROMPT_BOT_PUBLIC.format(**fmt)

        self.messages = [{"role": "system", "content": prompt}]

    async def chat(self, user_message: str) -> tuple[str, list[dict]]:
        """
        Run one user turn through the LLM tool-call loop.

        Returns ``(response_text, tool_calls_made)``.
        """
        import json as _json
        import openai
        from datetime import datetime, timedelta, timezone
        from verlihub.api.routes.llm import (
            _execute_tool, _get_openai_client,
            _endpoint_supports_tools, _inject_hub_context,
            _ACTION_RE, _extract_and_execute_actions,
        )
        from verlihub.api.auth import TokenData

        global _endpoint_supports_tools_bot
        # Use default endpoint from config
        bot_endpoint = self.llm_cfg.get_endpoint() if self.llm_cfg else None
        client = _get_openai_client(bot_endpoint)
        bot_model = bot_endpoint.model if bot_endpoint else "llama3.1"
        self.messages.append({"role": "user", "content": user_message})
        tool_calls_made: list[dict] = []

        # TokenData requires an ``exp`` field — use a far-future expiry
        fake_user = TokenData(
            nick=self.nick,
            user_class=self.user_class,
            exp=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        is_admin = (
            self.llm_cfg
            and self.user_class >= self.llm_cfg.admin_class
        )
        max_rounds = self.llm_cfg.max_tool_rounds if self.llm_cfg else 5

        # Check if tools are known to be unsupported by the endpoint
        use_tools = bool(self.tools) and _endpoint_supports_tools is not False

        for _round in range(max_rounds):
            kwargs: dict[str, Any] = dict(
                model=bot_model,
                messages=self.messages,
                temperature=self.llm_cfg.temperature if self.llm_cfg else 0.3,
                max_tokens=self.llm_cfg.max_tokens if self.llm_cfg else 2048,
            )
            if use_tools:
                kwargs["tools"] = self.tools
                kwargs["tool_choice"] = "auto"

            try:
                response = await client.chat.completions.create(**kwargs)
            except (openai.BadRequestError, openai.PermissionDeniedError) as exc:
                if _round == 0 and use_tools:
                    # Endpoint rejects tool_choice — fall back to context injection
                    log.warning("Bot: tool calling not supported, falling back: %s", exc)
                    use_tools = False
                    # Update module-level flag so dashboard also knows
                    import verlihub.api.routes.llm as _llm_mod
                    _llm_mod._endpoint_supports_tools = False
                    _inject_hub_context(self.messages, fake_user, is_admin)
                    response = await client.chat.completions.create(
                        model=kwargs["model"],
                        messages=self.messages,
                        temperature=kwargs["temperature"],
                        max_tokens=kwargs["max_tokens"],
                    )
                else:
                    raise

            choice = response.choices[0]
            msg = choice.message
            self.messages.append(msg.model_dump())

            # If not using native tools, check for <action> blocks
            if not use_tools:
                text = msg.content or "(no response)"
                if _ACTION_RE.search(text):
                    action_results = await _extract_and_execute_actions(
                        text, fake_user, is_admin,
                    )
                    if action_results:
                        for ar in action_results:
                            tool_calls_made.append({"name": ar["name"], "args": ar["args"]})
                        # Strip action blocks from visible text
                        clean = _ACTION_RE.sub("", text).strip()
                        return clean or "Done.", tool_calls_made
                return text, tool_calls_made

            if not msg.tool_calls:
                return msg.content or "(no response)", tool_calls_made

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = (
                        _json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                except Exception:
                    fn_args = {}
                log.info("Bot tool call (%s→%s): %s(%s)", self.nick, self.bot_nick, fn_name, fn_args)
                tool_calls_made.append({"name": fn_name, "args": fn_args})
                result = await _execute_tool(fn_name, fn_args, fake_user, is_admin)
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )

        # Exhausted rounds
        self.messages.append(
            {
                "role": "user",
                "content": "(System: tool call limit reached. Provide your answer with data collected so far.)",
            }
        )
        kwargs_final: dict[str, Any] = dict(
            model=bot_model,
            messages=self.messages,
            temperature=self.llm_cfg.temperature if self.llm_cfg else 0.3,
            max_tokens=self.llm_cfg.max_tokens if self.llm_cfg else 2048,
        )
        response = await client.chat.completions.create(**kwargs_final)
        return response.choices[0].message.content or "(no response)", tool_calls_made


# ---------------------------------------------------------------------------
# Handler that plugs into the C++ event system
# ---------------------------------------------------------------------------

# regex to detect bot addressed in main chat: "Bot-Nick: msg" or "Bot-Nick, msg"
_MENTION_RE: re.Pattern | None = None

# Active sessions  —  keyed "pm:{nick}" or "chat:{nick}"
_sessions: dict[str, BotChatSession] = {}
_sessions_lock = threading.Lock()


def _get_or_create_session(
    key: str,
    nick: str,
    user_class: int,
    bot_nick: str,
    hub_name: str,
    mode: str,
    llm_cfg: Any,
) -> BotChatSession:
    with _sessions_lock:
        session = _sessions.get(key)
        if session is None:
            session = BotChatSession(
                nick, user_class, bot_nick, hub_name,
                mode=mode, llm_cfg=llm_cfg,
            )
            _sessions[key] = session
        return session


class BotChatHandler:
    """
    Registers hub event handlers to route messages to the LLM bot.

    Instantiate once during application lifespan and call :meth:`register`
    with the :class:`HubEventHandler`.  Call :meth:`shutdown` to clean up.
    """

    def __init__(self, ctx: "HubContext", llm_cfg: Any = None):
        self.ctx = ctx
        self.llm_cfg = llm_cfg
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bot_nick: str = "Hub-Security"
        self._hub_name: str = "Verlihub"

        # Resolve names from config
        try:
            from verlihub.config import get_config_optional
            cfg = get_config_optional()
            if cfg:
                self._bot_nick = cfg.bots.security.nick
                self._hub_name = cfg.hub.name
        except Exception:
            pass

        # Pre-compile mention regex
        global _MENTION_RE
        escaped = re.escape(self._bot_nick)
        _MENTION_RE = re.compile(
            rf"^{escaped}\s*[:,]\s*(.+)", re.DOTALL | re.IGNORECASE
        )

    # -- public API --------------------------------------------------------

    def register(self, events: "HubEventHandler") -> None:
        """Wire into the hub event system."""
        events.register("private_message", self._on_pm)
        events.register("chat_message", self._on_chat)
        log.info(
            "Bot chat registered — bot=%s, hub=%s",
            self._bot_nick, self._hub_name,
        )

    def unregister(self, events: "HubEventHandler") -> None:
        """Remove handlers (called on shutdown)."""
        events.unregister("private_message", self._on_pm)
        events.unregister("chat_message", self._on_chat)

    def shutdown(self) -> None:
        """Clear sessions."""
        with _sessions_lock:
            _sessions.clear()

    # -- event callbacks (called from C++ I/O thread) ----------------------

    def _get_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Return the running asyncio event loop."""
        if self._loop is not None and self._loop.is_running():
            return self._loop
        # Use the event loop stored by HubEventHandler.set_event_loop()
        try:
            ev_loop = self.ctx.events._event_loop
            if ev_loop is not None and ev_loop.is_running():
                self._loop = ev_loop
                return ev_loop
        except AttributeError:
            pass
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._loop = loop
                return loop
        except RuntimeError:
            pass
        return None

    def _on_pm(self, from_nick: str, to_nick: str, message: str) -> bool:
        """Handle PMs directed at the security bot."""
        if to_nick != self._bot_nick:
            return True  # not for us — let it through

        if not self.llm_cfg or not self.llm_cfg.enabled:
            self._send_pm(
                from_nick,
                "AI chat is currently disabled. Please contact a hub operator.",
            )
            return True

        log.info("Bot PM from %s: %s", from_nick, message[:120])

        loop = self._get_loop()
        if loop is None:
            log.warning("No asyncio loop — cannot process bot PM")
            self._send_pm(
                from_nick,
                "I'm not fully started yet — please try again in a moment.",
            )
            return True

        # Fire-and-forget the async LLM call
        asyncio.run_coroutine_threadsafe(
            self._handle_pm_async(from_nick, message), loop
        )

        # Return True — the C++ side will try to deliver the PM to
        # Hub-Security which isn't a real connection, so it silently fails.
        return True

    def _on_chat(self, nick: str, message: str) -> bool:
        """Handle main-chat messages that mention the bot."""
        if _MENTION_RE is None:
            return True

        m = _MENTION_RE.match(message)
        if not m:
            return True  # not addressed to the bot

        if not self.llm_cfg or not self.llm_cfg.enabled:
            return True

        body = m.group(1).strip()
        if not body:
            return True

        log.info("Bot chat mention from %s: %s", nick, body[:120])

        loop = self._get_loop()
        if loop is None:
            log.warning("No asyncio loop — cannot process bot chat")
            return True

        asyncio.run_coroutine_threadsafe(
            self._handle_chat_async(nick, body), loop
        )

        return True  # let the original message through to other users

    # -- async LLM handlers -----------------------------------------------

    async def _handle_pm_async(self, nick: str, message: str) -> None:
        """Process a PM to the bot via the LLM pipeline."""
        try:
            user_class = self._get_user_class(nick)
            session = _get_or_create_session(
                f"pm:{nick}",
                nick,
                user_class,
                self._bot_nick,
                self._hub_name,
                mode="pm",
                llm_cfg=self.llm_cfg,
            )
            response_text, _tools = await session.chat(message)
            self._send_pm(nick, response_text)
        except ImportError:
            log.warning("openai package not installed — bot chat unavailable")
            self._send_pm(
                nick,
                "AI chat is not available (missing dependencies). "
                "Please contact a hub operator.",
            )
        except Exception as exc:
            log.exception("Bot PM handler error for %s", nick)
            # Provide a user-friendly message depending on error type
            err_msg = str(exc).lower()
            if "connection" in err_msg or "refused" in err_msg or "timeout" in err_msg:
                self._send_pm(
                    nick,
                    "The AI backend is temporarily unreachable. Please try again later.",
                )
            else:
                self._send_pm(
                    nick,
                    "Sorry, I encountered an error processing your message.",
                )

    async def _handle_chat_async(self, nick: str, message: str) -> None:
        """Process a main-chat mention via the LLM pipeline (no tools).

        All users share one public chat session (``chat:public``) so
        conversation context is visible and consistent for everyone in
        the main chat room.
        """
        try:
            user_class = self._get_user_class(nick)
            session = _get_or_create_session(
                "chat:public",
                nick,
                user_class,
                self._bot_nick,
                self._hub_name,
                mode="chat",
                llm_cfg=self.llm_cfg,
            )
            response_text, _tools = await session.chat(message)
            self._send_chat(response_text)
        except ImportError:
            log.warning("openai package not installed — bot chat unavailable")
        except Exception:
            log.exception("Bot chat handler error for %s", nick)

    # -- helpers -----------------------------------------------------------

    def _get_user_class(self, nick: str) -> int:
        """Look up the user's class from the live hub context."""
        try:
            info = self.ctx.get_user_info(nick)
            if info:
                return info.get("user_class", -1)
        except Exception:
            log.debug("Could not look up class for %s", nick)
        return -1  # GUEST

    def _send_pm(self, to_nick: str, message: str) -> None:
        """Send a PM from the bot to a user (raw NMDC format)."""
        try:
            self.ctx.send_pm_as(self._bot_nick, to_nick, message)
        except Exception:
            log.exception("Failed to send bot PM to %s", to_nick)

    def _send_chat(self, message: str) -> None:
        """Send a main-chat message from the bot."""
        try:
            self.ctx.send_chat_as(self._bot_nick, message)
        except Exception:
            log.exception("Failed to send bot chat message")

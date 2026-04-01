"""
NMDC Bot Chat — LLM-powered hub security bot.

When users send a private message to the hub security bot (``Hub-Security``),
this module intercepts it via the C++ event callback and forwards the message
to the LLM chat pipeline.  The bot's reply is sent back as an NMDC PM.

Users can also address the bot in main chat.  The ``chat_mode`` setting
controls how eagerly the bot responds:

  * ``"direct"``  — only when addressed by name (``Hub-Security: hi``)
  * ``"mention"`` — whenever the bot name appears anywhere in the message
  * ``"keyword"`` — bot name **or** any word in ``triggers`` list

Main-chat interactions always operate at the **lowest** security level — no
tools, purely conversational.

Private messages respect the sender's actual user class:
  * class ≥ ``admin_class`` → admin tools (kick/ban/config)
  * class ≥ ``min_class``   → read-only hub tools
  * lower classes           → conversational only (no tools)

Thinking feedback
~~~~~~~~~~~~~~~~~
While the LLM is processing, the bot:
  1. Broadcasts an updated ``$MyINFO`` to show "⏳ Thinking…" in its
     description (visible in most DC++ client user-lists).
  2. Sends periodic "Thinking…" PM hints (interval from config).
  3. Resets description to idle when done or on error.

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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from verlihub.bot.memory import BotMemory
    from verlihub.bot.mood import BotMoodEngine
    from verlihub.config import BotBehaviorConfig
    from verlihub.core import HubContext, HubEventHandler

log = logging.getLogger("verlihub.bot.chat")


def _strip_think_blocks(text: str | None) -> str:
    """Remove ``<think>…</think>`` reasoning blocks from LLM output.

    Handles both properly closed and unterminated ``<think>`` blocks.
    If stripping would remove *all* visible content, falls back to
    returning the text with just the tags removed.
    """
    if not text:
        return text or ""
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', text).lstrip()
    idx = cleaned.find('<think>')
    if idx >= 0:
        cleaned = cleaned[:idx].rstrip()
    if not cleaned.strip() and text.strip():
        cleaned = text.replace('<think>', '').replace('</think>', '').strip()
    return cleaned

# ---------------------------------------------------------------------------
# System prompts
#
# Placeholders:
#   {personality}   — static persona from BotBehaviorConfig
#   {mood}          — dynamic mood modifier from BotMoodEngine
#   {memory}        — summary of stored notes from BotMemory
#   {current_time}  — current UTC date/time for temporal awareness
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_BOT_ADMIN = """\
{personality}\
You are {bot_nick}, the security bot of the "{hub_name}" DC++ hub.  You are \
chatting privately with {user_nick} (class {user_class}), who has administrator \
privileges.

Current date and time (UTC): {current_time}

You have access to tools that query and control the live hub. Use them to \
answer questions accurately — never guess hub state, always check via tools.

Available capabilities:
- Query online users, operators, bots
- View hub statistics, geographic distribution, share statistics
- Look up individual user details (including IP addresses)
- Kick users, send broadcasts, send private messages
- Execute hub console commands (!help for list)
- Read and write hub configuration

FORMATTING RULES (IMPORTANT):
- This is an NMDC chat client that does NOT render markdown
- NEVER use markdown: no **bold**, no *italic*, no # headings, no ```code blocks```
- NEVER use markdown tables (no | pipes)
- For tabular data use fixed-width columns with spaces
- Use plain ASCII text only
- Keep responses concise and conversational
- Always call tools rather than assuming hub state
- Format numbers readably (e.g. "1.23 TiB" not raw bytes)
{mood}\
{memory}\
"""

SYSTEM_PROMPT_BOT_USER = """\
{personality}\
You are {bot_nick}, the security bot of the "{hub_name}" DC++ hub.  You are \
chatting privately with {user_nick} (class {user_class}).

Current date and time (UTC): {current_time}

You have access to read-only tools that show hub information.  Use \
them to answer questions accurately.

You can see: hub info, online users (nicknames, IPs, countries, share sizes, \
user classes, client tags, descriptions), operators, geographic distribution, \
and share statistics.  This is all publicly visible information that clients \
broadcast via MyINFO.

You CANNOT: kick users, ban users, change configuration, \
execute console commands, or send messages on behalf of users.

FORMATTING RULES (IMPORTANT):
- This is an NMDC chat client that does NOT render markdown
- NEVER use markdown: no **bold**, no *italic*, no # headings, no ```code blocks```
- NEVER use markdown tables (no | pipes)
- For tabular data use fixed-width columns with spaces
- Use plain ASCII text only
- Keep responses concise and conversational
- Always call tools rather than guessing
{mood}\
{memory}\
"""

SYSTEM_PROMPT_BOT_PUBLIC = """\
{personality}\
You are {bot_nick}, the bot of the "{hub_name}" DC++ hub.  You are in \
the main public chat room.  Multiple users may talk to you — each message \
is prefixed with their nickname like "[SomeUser]: message".  All users \
can see this conversation.

Current date and time (UTC): {current_time}

You have NO tools and NO access to hub internals.  You can only hold a \
friendly, general-purpose conversation.

FORMATTING RULES (IMPORTANT):
- This is an NMDC chat client that does NOT render markdown
- NEVER use markdown: no **bold**, no *italic*, no # headings, no ```code blocks```
- NEVER use markdown tables (no | pipes)
- For tabular data use fixed-width columns with spaces
- Use plain ASCII text only
- Keep responses short and conversational — this is a public chat room
- Do NOT make up information about the hub (users, status, uptime, etc.)
- If asked about hub status, suggest the user PM you for detailed info
- Never reveal private information about users
- Maximum response length: roughly {max_chat_length} characters
- Address users by name when replying to make multi-user chat clear
{mood}\
{memory}\
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
        behavior: "BotBehaviorConfig | None" = None,
        mood_engine: "BotMoodEngine | None" = None,
        memory: "BotMemory | None" = None,
    ):
        self.nick = nick
        self.user_class = user_class
        self.bot_nick = bot_nick
        self.hub_name = hub_name
        self.mode = mode
        self.llm_cfg = llm_cfg
        self.behavior = behavior
        self.mood_engine = mood_engine
        self.memory = memory
        self.created_at = time.time()
        self.last_activity = time.time()
        self.messages: list[dict] = []
        self.tools: list[dict] = []
        self._base_system_prompt: str = ""

        self._build(nick, user_class, bot_nick, hub_name, mode, llm_cfg, behavior)

    # -- internal ---------------------------------------------------------

    def _build(
        self,
        nick: str,
        user_class: int,
        bot_nick: str,
        hub_name: str,
        mode: str,
        llm_cfg: Any,
        behavior: "BotBehaviorConfig | None" = None,
    ) -> None:
        """Select the system prompt and tool set based on context."""
        from verlihub.api.routes.llm import _build_admin_tools, _build_readonly_tools

        personality_text = ""
        if behavior and behavior.personality:
            personality_text = f"PERSONALITY AND VOICE (always stay in character): {behavior.personality}\n\n"

        max_chat_length = behavior.max_chat_length if behavior else 400

        # Mood and memory are injected fresh on each chat() call;
        # keep {mood}, {memory}, {current_time} as literal placeholders
        # so _refresh_system_prompt() can replace them each turn.
        fmt = dict(
            bot_nick=bot_nick,
            hub_name=hub_name,
            user_nick=nick,
            user_class=user_class,
            personality=personality_text,
            max_chat_length=max_chat_length,
        )

        # Use a two-phase format: first substitute static fields,
        # then leave dynamic placeholders intact for _refresh_system_prompt().
        # Double-brace the dynamic placeholders so .format() preserves them.
        def _prepare_template(template: str) -> str:
            return (
                template
                .replace("{mood}", "{{mood}}")
                .replace("{memory}", "{{memory}}")
                .replace("{current_time}", "{{current_time}}")
            )

        if mode == "chat":
            # Main chat — no hub tools, lowest security
            self.tools = []
            prompt = _prepare_template(SYSTEM_PROMPT_BOT_PUBLIC).format(**fmt)
        else:
            # PM — tools depend on user class
            admin_class = llm_cfg.admin_class if llm_cfg else 5
            min_class = llm_cfg.min_class if llm_cfg else 3

            if user_class >= admin_class:
                self.tools = _build_readonly_tools() + _build_admin_tools()
                prompt = _prepare_template(SYSTEM_PROMPT_BOT_ADMIN).format(**fmt)
            elif user_class >= min_class:
                self.tools = _build_readonly_tools()
                prompt = _prepare_template(SYSTEM_PROMPT_BOT_USER).format(**fmt)
            else:
                self.tools = []
                prompt = _prepare_template(SYSTEM_PROMPT_BOT_PUBLIC).format(**fmt)

        # ── Attach web & memory tools (available in all modes) ──
        web_enabled = behavior.web_enabled if behavior else False
        memory_enabled = behavior.memory_enabled if behavior else False

        if web_enabled:
            from verlihub.bot.web import build_web_tools
            self.tools += build_web_tools()

        if memory_enabled:
            from verlihub.bot.memory import build_memory_tools
            self.tools += build_memory_tools()

        self._base_system_prompt = prompt
        self.messages = [{"role": "system", "content": prompt}]

    async def _refresh_system_prompt(self) -> None:
        """Re-inject dynamic mood, memory, and current time into the prompt.

        Called at the start of every ``chat()`` turn so the LLM sees the
        bot's current emotional state, memory summary, and time of day.
        """
        mood_text = ""
        if self.mood_engine:
            mt = self.mood_engine.get_mood_text()
            if mt:
                mood_text = f"\nCurrent mood: {mt}\n"

        memory_text = ""
        if self.memory:
            try:
                ms = await self.memory.get_context_summary()
                if ms:
                    memory_text = f"\n{ms}\n"
            except Exception:
                pass

        # Update current time using hub timezone if configured
        hub_tz_name = ""
        if self.behavior:
            try:
                from verlihub.config import get_config_optional
                _cfg = get_config_optional()
                if _cfg and _cfg.hub:
                    hub_tz_name = _cfg.hub.timezone
            except Exception:
                pass
        if hub_tz_name and hub_tz_name != "UTC":
            try:
                from zoneinfo import ZoneInfo
                hub_tz = ZoneInfo(hub_tz_name)
                current_time = datetime.now(hub_tz).strftime(f"%Y-%m-%d %H:%M {hub_tz_name}")
            except Exception:
                current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        else:
            current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Replace the {mood}, {memory}, and {current_time} placeholders
        updated = self._base_system_prompt
        if "{mood}" in updated:
            updated = updated.replace("{mood}", mood_text)
            updated = updated.replace("{memory}", memory_text)
        else:
            # Fallback: append to base prompt
            updated = updated + mood_text + memory_text

        if "{current_time}" in updated:
            updated = updated.replace("{current_time}", current_time)

        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = updated

    async def _execute_bot_tool(
        self, fn_name: str, fn_args: dict[str, Any],
    ) -> str | None:
        """Try to execute a bot-specific tool (web or memory).

        Returns the result string, or ``None`` if *fn_name* is not a
        bot tool (caller should fall through to hub tools).
        """
        # Memory tools
        if self.memory and fn_name in ("save_note", "recall_notes", "list_notes", "delete_note"):
            from verlihub.bot.memory import execute_memory_tool
            return await execute_memory_tool(self.memory, fn_name, fn_args)

        # Web tools
        web_enabled = self.behavior.web_enabled if self.behavior else False
        if web_enabled and fn_name in ("web_search", "fetch_webpage", "read_rss"):
            from verlihub.bot.web import execute_web_tool
            return await execute_web_tool(fn_name, fn_args)

        return None  # not a bot tool

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

        # ── Refresh dynamic context in the system prompt ──
        await self._refresh_system_prompt()

        global _endpoint_supports_tools_bot
        # Use the endpoint specified in bot behavior config, falling back to default
        bot_endpoint_name = (
            self.behavior.endpoint if self.behavior and self.behavior.endpoint else ""
        )
        bot_endpoint = (
            self.llm_cfg.get_endpoint(bot_endpoint_name)
            if self.llm_cfg
            else None
        )
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
                text = _strip_think_blocks(msg.content) or "(no response)"
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
                return _strip_think_blocks(msg.content) or "(no response)", tool_calls_made

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

                # Route to bot-specific tools first (web, memory), then hub tools
                result = await self._execute_bot_tool(fn_name, fn_args)
                if result is None:
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
        return _strip_think_blocks(response.choices[0].message.content) or "(no response)", tool_calls_made

    async def chat_stream(self, user_message: str):
        """Stream one user turn, yielding sentence/paragraph chunks.

        This is an async generator that yields ``str`` chunks as they
        become available.  The full message is appended to history once
        complete.  Tool calls are executed silently and the final text
        response is streamed.
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

        await self._refresh_system_prompt()

        global _endpoint_supports_tools_bot
        bot_endpoint_name = (
            self.behavior.endpoint if self.behavior and self.behavior.endpoint else ""
        )
        bot_endpoint = (
            self.llm_cfg.get_endpoint(bot_endpoint_name)
            if self.llm_cfg
            else None
        )
        client = _get_openai_client(bot_endpoint)
        bot_model = bot_endpoint.model if bot_endpoint else "llama3.1"
        self.messages.append({"role": "user", "content": user_message})

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
        use_tools = bool(self.tools) and _endpoint_supports_tools is not False

        for _round in range(max_rounds):
            kwargs: dict[str, Any] = dict(
                model=bot_model,
                messages=self.messages,
                temperature=self.llm_cfg.temperature if self.llm_cfg else 0.3,
                max_tokens=self.llm_cfg.max_tokens if self.llm_cfg else 2048,
                stream=True,
            )
            if use_tools:
                kwargs["tools"] = self.tools
                kwargs["tool_choice"] = "auto"

            try:
                stream = await client.chat.completions.create(**kwargs)
            except (openai.BadRequestError, openai.PermissionDeniedError) as exc:
                if _round == 0 and use_tools:
                    log.warning("Bot stream: tool calling not supported, falling back: %s", exc)
                    use_tools = False
                    import verlihub.api.routes.llm as _llm_mod
                    _llm_mod._endpoint_supports_tools = False
                    _inject_hub_context(self.messages, fake_user, is_admin)
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    stream = await client.chat.completions.create(**kwargs)
                else:
                    raise
            except Exception:
                # Streaming not supported — fall back to non-streaming
                kwargs.pop("stream", None)
                response = await client.chat.completions.create(**kwargs)
                text = _strip_think_blocks(response.choices[0].message.content) or "(no response)"
                self.messages.append(response.choices[0].message.model_dump())
                yield text
                return

            # Consume the stream
            content_parts: list[str] = []
            tool_calls_acc: dict[int, dict] = {}
            _in_think = False
            _raw_buf = ""  # Accumulates raw text for think-block detection

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    token = delta.content
                    content_parts.append(token)
                    _raw_buf += token

                    # Process accumulated buffer for <think> blocks.
                    # We buffer because tags may span multiple tokens.
                    while True:
                        if _in_think:
                            end_idx = _raw_buf.find("</think>")
                            if end_idx == -1:
                                # Still inside think block — consume buffer
                                _raw_buf = ""
                                break
                            # Found end tag — skip everything up to and including </think>
                            _raw_buf = _raw_buf[end_idx + len("</think>"):]
                            _in_think = False
                        else:
                            start_idx = _raw_buf.find("<think>")
                            if start_idx == -1:
                                # No think tag — yield visible content
                                # But keep a small tail in case a partial "<think" is at the end
                                if len(_raw_buf) > 7:
                                    to_yield = _raw_buf[:-7]
                                    _raw_buf = _raw_buf[-7:]
                                    if to_yield:
                                        yield to_yield
                                break
                            else:
                                # Yield content before the <think> tag
                                before = _raw_buf[:start_idx]
                                if before:
                                    yield before
                                _raw_buf = _raw_buf[start_idx + len("<think>"):]
                                _in_think = True

                if delta.tool_calls:
                    for tc_d in delta.tool_calls:
                        idx = tc_d.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_d.id:
                            tool_calls_acc[idx]["id"] = tc_d.id
                        if tc_d.function:
                            if tc_d.function.name:
                                tool_calls_acc[idx]["name"] += tc_d.function.name
                            if tc_d.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc_d.function.arguments

            # Flush any remaining visible content from the buffer.
            # If the stream ended while inside a <think> block (incomplete
            # thinking, timeout, or model that only produces reasoning),
            # fall back to the regex-based stripper on the full content so
            # we still yield whatever visible text was produced.
            if _raw_buf and not _in_think:
                yield _raw_buf
                _raw_buf = ""

            full_content = "".join(content_parts)

            if _in_think and full_content:
                # Stream ended mid-think — extract any visible text
                fallback = _strip_think_blocks(full_content)
                if fallback:
                    yield fallback
            msg_dict: dict = {"role": "assistant", "content": full_content or None}
            if tool_calls_acc:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
                ]
            self.messages.append(msg_dict)

            if not tool_calls_acc:
                # No tool calls — streaming is done
                return

            # Execute tool calls and loop for next round
            for tc in [tool_calls_acc[i] for i in sorted(tool_calls_acc)]:
                fn_name = tc["name"]
                try:
                    fn_args = _json.loads(tc["arguments"]) if tc["arguments"] else {}
                except Exception:
                    fn_args = {}
                log.info("Bot tool call (%s→%s): %s(%s)", self.nick, self.bot_nick, fn_name, fn_args)

                result = await self._execute_bot_tool(fn_name, fn_args)
                if result is None:
                    result = await _execute_tool(fn_name, fn_args, fake_user, is_admin)
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc["id"], "content": result}
                )

        # Exhausted rounds — yield a summary
        yield "(working...)"


# Sentence/paragraph splitter for chunked NMDC delivery
_SENTENCE_RE = re.compile(r'(?<=[.!?…])\s+|(?<=\n)\s*')


def _chunk_sentences(text: str, min_chunk: int = 60) -> list[str]:
    """Split text into sentence-sized chunks for progressive delivery.

    Groups short sentences together so each chunk is at least
    *min_chunk* characters (except the last one).
    """
    parts = _SENTENCE_RE.split(text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if buf:
            buf += " " + part
        else:
            buf = part
        if len(buf) >= min_chunk:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks:
            chunks.append(buf)
        else:
            chunks = [buf]
    return chunks

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
    behavior: "BotBehaviorConfig | None" = None,
    mood_engine: "BotMoodEngine | None" = None,
    memory: "BotMemory | None" = None,
) -> BotChatSession:
    session_timeout = behavior.session_timeout if behavior else 7200
    with _sessions_lock:
        session = _sessions.get(key)
        if session is not None:
            # Check if session has expired
            if session_timeout > 0 and (time.time() - session.last_activity) > session_timeout:
                log.info("Bot session %s expired (idle %.0fs > %ds), starting fresh",
                         key, time.time() - session.last_activity, session_timeout)
                session = None  # will create a new one below
        if session is None:
            session = BotChatSession(
                nick, user_class, bot_nick, hub_name,
                mode=mode, llm_cfg=llm_cfg, behavior=behavior,
                mood_engine=mood_engine, memory=memory,
            )
            _sessions[key] = session
        else:
            # Update references so existing sessions pick up current mood
            session.mood_engine = mood_engine
            session.memory = memory
            session.last_activity = time.time()
        return session


def get_nmdc_sessions_for_user(nick: str) -> list[dict]:
    """Return metadata about NMDC PM sessions for *nick*.

    Used by the dashboard to list sessions that originated in the NMDC
    client so the user can continue them from the web UI.
    """
    prefix = f"pm:{nick}"
    results: list[dict] = []
    with _sessions_lock:
        for key, session in _sessions.items():
            if key == prefix:
                results.append({
                    "session_id": f"nmdc-{nick}",
                    "title": f"NMDC chat with {session.bot_nick}",
                    "created_at": session.created_at,
                    "last_activity": session.last_activity,
                    "message_count": max(0, len(session.messages) - 1),  # exclude system
                    "source": "nmdc",
                })
    return results


def get_nmdc_session_messages(nick: str) -> list[dict] | None:
    """Return the message history of an NMDC PM session.

    Returns ``None`` if no session exists for *nick*.
    """
    key = f"pm:{nick}"
    with _sessions_lock:
        session = _sessions.get(key)
        if session is None:
            return None
        session.last_activity = time.time()
        # Return a copy excluding the system prompt
        return [m for m in session.messages if m.get("role") != "system"]


class BotChatHandler:
    """
    Registers hub event handlers to route messages to the LLM bot.

    Instantiate once during application lifespan and call :meth:`register`
    with the :class:`HubEventHandler`.  Call :meth:`shutdown` to clean up.
    """

    def __init__(
        self,
        ctx: "HubContext",
        llm_cfg: Any = None,
        behavior: "BotBehaviorConfig | None" = None,
    ):
        self.ctx = ctx
        self.llm_cfg = llm_cfg
        self.behavior = behavior
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bot_nick: str = "Hub-Security"
        self._hub_name: str = "Verlihub"
        self._bot_description: str = "Hub security system"
        self._bot_email: str = ""
        self._proactive_task: Optional[asyncio.Task] = None
        self._mood_task: Optional[asyncio.Task] = None
        self._mood_engine: "BotMoodEngine | None" = None
        self._memory: "BotMemory | None" = None

        # ── Main-chat message batching ──
        # Collect messages arriving within a short window so the bot
        # can respond to all of them at once instead of firing separate
        # LLM calls per message.
        self._chat_batch: list[tuple[str, str]] = []  # [(nick, body), ...]
        self._chat_batch_lock = threading.Lock()
        self._chat_batch_task: Optional[asyncio.Task] = None
        self._chat_batch_delay: float = 2.5  # seconds to wait for more msgs

        # Resolve names from config
        try:
            from verlihub.config import get_config_optional
            cfg = get_config_optional()
            if cfg:
                self._bot_nick = cfg.bots.security.nick
                self._hub_name = cfg.hub.name
                self._bot_description = cfg.bots.security.description
                self._bot_email = cfg.bots.security.email
                # Merge behavior from config if not explicitly passed
                if self.behavior is None:
                    self.behavior = cfg.bots.behavior
        except Exception:
            pass

        # Instantiate mood engine if enabled
        if self.behavior and self.behavior.mood_enabled:
            try:
                from verlihub.bot.mood import BotMoodEngine
                self._mood_engine = BotMoodEngine(
                    interaction_window=self.behavior.mood_window,
                    user_history_window=self.behavior.mood_user_history,
                    low_interaction_threshold=self.behavior.mood_low_interaction,
                    high_interaction_threshold=self.behavior.mood_high_interaction,
                    low_user_ratio=self.behavior.mood_low_user_ratio,
                    high_user_ratio=self.behavior.mood_high_user_ratio,
                )
                log.info(
                    "Bot mood engine enabled (window=%ds, thresholds=%.1f/%.1f, user_ratio=%.2f/%.2f)",
                    self.behavior.mood_window,
                    self.behavior.mood_low_interaction,
                    self.behavior.mood_high_interaction,
                    self.behavior.mood_low_user_ratio,
                    self.behavior.mood_high_user_ratio,
                )
            except Exception:
                log.warning("Failed to initialise mood engine", exc_info=True)

        # Instantiate persistent memory (uses the shared application database)
        if self.behavior and self.behavior.memory_enabled:
            try:
                from verlihub.bot.memory import BotMemory
                mood_fn = (
                    (lambda: self._mood_engine.get_mood().name)
                    if self._mood_engine
                    else None
                )
                self._memory = BotMemory(mood_fn=mood_fn)
                log.info("Bot memory enabled (shared database)")
            except Exception:
                log.warning("Failed to initialise bot memory", exc_info=True)

        # Pre-compile mention regex (for "direct" mode — exact prefix match)
        self._mention_direct_re: re.Pattern | None = None
        self._mention_anywhere_re: re.Pattern | None = None
        self._keyword_re: re.Pattern | None = None
        self._rebuild_patterns()

    def _rebuild_patterns(self) -> None:
        """Compile chat-matching patterns from bot nick + behavior config."""
        escaped = re.escape(self._bot_nick)

        # "direct" — Bot-Nick: msg  or  Bot-Nick, msg
        self._mention_direct_re = re.compile(
            rf"^{escaped}\s*[:,]\s*(.+)", re.DOTALL | re.IGNORECASE
        )

        # "mention" — bot name appears anywhere in the message
        self._mention_anywhere_re = re.compile(
            rf"\b{escaped}\b", re.IGNORECASE
        )

        # "keyword" — bot name OR trigger words
        triggers: list[str] = []
        if self.behavior and self.behavior.triggers:
            triggers = [t.strip() for t in self.behavior.triggers if t.strip()]
        all_terms = [escaped] + [re.escape(t) for t in triggers]
        pattern = "|".join(all_terms)
        self._keyword_re = re.compile(
            rf"\b(?:{pattern})\b", re.IGNORECASE
        )

    # -- NMDC MyINFO helpers (bot description updates) --------------------

    def _broadcast_bot_description(self, description: str) -> None:
        """Broadcast an updated $MyINFO for the bot nick to all clients.

        This updates the bot's user-list entry (description / comment)
        in real-time for all connected DC++ clients.
        """
        try:
            nick = self._bot_nick
            email = self._bot_email or ""
            # NMDC $MyINFO format for a bot user
            myinfo = (
                f"$MyINFO $ALL {nick} {description}"
                f"$ $Bot\x01${email}$0$"
            )
            self.ctx.send_to_all(myinfo)
        except Exception:
            log.debug("Failed to broadcast bot description update", exc_info=True)

    def _set_thinking(self) -> None:
        """Set bot description to "thinking" state."""
        self._broadcast_bot_description(
            f"{self._bot_description} ⏳ Thinking…"
        )

    def _set_idle(self) -> None:
        """Reset bot description to normal idle state."""
        self._broadcast_bot_description(self._bot_description)

    # -- public API --------------------------------------------------------

    def register(self, events: "HubEventHandler") -> None:
        """Wire into the hub event system."""
        events.register("private_message", self._on_pm)
        events.register("chat_message", self._on_chat)

        chat_mode = self.behavior.chat_mode if self.behavior else "direct"
        log.info(
            "Bot chat registered — bot=%s, hub=%s, chat_mode=%s",
            self._bot_nick, self._hub_name, chat_mode,
        )

    def unregister(self, events: "HubEventHandler") -> None:
        """Remove handlers (called on shutdown)."""
        events.unregister("private_message", self._on_pm)
        events.unregister("chat_message", self._on_chat)

    def shutdown(self) -> None:
        """Clear sessions and stop proactive / mood tasks."""
        if self._proactive_task and not self._proactive_task.done():
            self._proactive_task.cancel()
        if self._mood_task and not self._mood_task.done():
            self._mood_task.cancel()
        with _sessions_lock:
            _sessions.clear()

    def start_proactive(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the proactive message timer and mood sampler (if configured)."""
        interval = self.behavior.proactive_interval if self.behavior else 0
        if interval > 0 and self.behavior and self.behavior.proactive_prompts:
            self._proactive_task = loop.create_task(self._proactive_loop())
            log.info("Bot proactive messages enabled (interval=%ds)", interval)

        # Start mood sampling independently of proactive messages
        if self._mood_engine is not None:
            self._mood_task = loop.create_task(self._mood_sample_loop())
            log.info("Bot mood sampling started (every 5 min)")

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

        return True

    def _on_chat(self, nick: str, message: str) -> bool:
        """Handle main-chat messages based on the configured chat_mode."""
        if not self.llm_cfg or not self.llm_cfg.enabled:
            return True

        chat_mode = self.behavior.chat_mode if self.behavior else "direct"
        body: str | None = None

        if chat_mode == "direct":
            # Only respond when directly addressed: "Bot-Nick: msg"
            if self._mention_direct_re is None:
                return True
            m = self._mention_direct_re.match(message)
            if m:
                body = m.group(1).strip()

        elif chat_mode == "mention":
            # Respond when bot name appears anywhere in the message
            if self._mention_anywhere_re and self._mention_anywhere_re.search(message):
                # Try to extract a direct address body first, else use full msg
                m = (
                    self._mention_direct_re.match(message)
                    if self._mention_direct_re
                    else None
                )
                body = m.group(1).strip() if m else message

        elif chat_mode == "keyword":
            # Respond to bot name or trigger keywords
            if self._keyword_re and self._keyword_re.search(message):
                m = (
                    self._mention_direct_re.match(message)
                    if self._mention_direct_re
                    else None
                )
                body = m.group(1).strip() if m else message

        if not body:
            return True  # no match — pass through silently

        log.info("Bot chat match (%s) from %s: %s", chat_mode, nick, body[:120])

        loop = self._get_loop()
        if loop is None:
            log.warning("No asyncio loop — cannot process bot chat")
            return True

        # Add to batch and schedule a delayed flush.
        # If more messages arrive within the delay window they will be
        # grouped together so the bot responds to all of them at once.
        with self._chat_batch_lock:
            self._chat_batch.append((nick, body))

        asyncio.run_coroutine_threadsafe(
            self._schedule_chat_batch(), loop
        )

        return True  # let the original message through to other users

    async def _schedule_chat_batch(self) -> None:
        """Schedule processing of the accumulated chat batch.

        If a flush is already pending, this is a no-op — the new message
        will be picked up when the pending delay expires.
        """
        if self._chat_batch_task and not self._chat_batch_task.done():
            return  # a flush is already scheduled
        self._chat_batch_task = asyncio.ensure_future(self._flush_chat_batch())

    async def _flush_chat_batch(self) -> None:
        """Wait for the batch window then process all collected messages."""
        await asyncio.sleep(self._chat_batch_delay)

        # Drain the batch
        with self._chat_batch_lock:
            batch = list(self._chat_batch)
            self._chat_batch.clear()

        if not batch:
            return

        # Build a combined message with sender attribution
        if len(batch) == 1:
            nick, body = batch[0]
            combined = f"[{nick}]: {body}"
        else:
            parts = [f"[{nick}]: {body}" for nick, body in batch]
            combined = "\n".join(parts)

        # Use the first sender's nick for session creation
        first_nick = batch[0][0]
        await self._handle_chat_async(first_nick, combined)

    # -- async LLM handlers -----------------------------------------------

    async def _handle_pm_async(self, nick: str, message: str) -> None:
        """Process a PM to the bot via the LLM pipeline, with streaming delivery."""
        thinking_task: asyncio.Task | None = None
        try:
            self._set_thinking()

            thinking_interval = (
                self.behavior.thinking_interval if self.behavior else 15
            )
            if thinking_interval > 0:
                thinking_task = asyncio.ensure_future(
                    self._send_thinking_hints(nick, thinking_interval)
                )

            user_class = self._get_user_class(nick)
            session = _get_or_create_session(
                f"pm:{nick}",
                nick,
                user_class,
                self._bot_nick,
                self._hub_name,
                mode="pm",
                llm_cfg=self.llm_cfg,
                behavior=self.behavior,
                mood_engine=self._mood_engine,
                memory=self._memory,
            )

            # Stream and deliver in sentence-sized chunks
            buf = ""
            sent_any = False
            async for token in session.chat_stream(message):
                buf += token
                chunks = _chunk_sentences(buf, min_chunk=80)
                if len(chunks) > 1:
                    for chunk in chunks[:-1]:
                        if thinking_task and not thinking_task.done():
                            thinking_task.cancel()
                            thinking_task = None
                        self._send_pm(nick, chunk)
                        sent_any = True
                        await asyncio.sleep(0.3)
                    buf = chunks[-1]

            # Send any remaining content
            if buf.strip():
                if thinking_task and not thinking_task.done():
                    thinking_task.cancel()
                    thinking_task = None
                self._send_pm(nick, buf.strip())
                sent_any = True

            if not sent_any:
                self._send_pm(nick, "(no response)")

            if self._mood_engine:
                self._mood_engine.record_interaction()

        except ImportError:
            log.warning("openai package not installed — bot chat unavailable")
            self._send_pm(
                nick,
                "AI chat is not available (missing dependencies). "
                "Please contact a hub operator.",
            )
        except Exception as exc:
            log.exception("Bot PM handler error for %s", nick)
            err_msg = str(exc).lower()
            if "connection" in err_msg or "refused" in err_msg or "timeout" in err_msg:
                self._send_pm(
                    nick,
                    "⚠ The AI backend is temporarily unreachable. Please try again later.",
                )
            elif "does not exist" in err_msg or "404" in err_msg:
                self._send_pm(
                    nick,
                    "⚠ The configured AI model was not found. "
                    "Please contact an operator to check the LLM endpoint.",
                )
            else:
                self._send_pm(
                    nick,
                    "⚠ Sorry, I encountered an error processing your message. "
                    "Please try again or contact an operator.",
                )
        finally:
            if thinking_task and not thinking_task.done():
                thinking_task.cancel()
            self._set_idle()

    async def _handle_chat_async(self, nick: str, message: str) -> None:
        """Process a main-chat mention via the LLM pipeline with streaming.

        All users share one public chat session (``chat:public``) so
        conversation context is visible and consistent for everyone in
        the main chat room.  Responses are streamed in sentence-sized
        chunks for faster perceived latency.
        """
        try:
            self._set_thinking()

            user_class = self._get_user_class(nick)
            session = _get_or_create_session(
                "chat:public",
                nick,
                user_class,
                self._bot_nick,
                self._hub_name,
                mode="chat",
                llm_cfg=self.llm_cfg,
                behavior=self.behavior,
                mood_engine=self._mood_engine,
                memory=self._memory,
            )

            max_len = self.behavior.max_chat_length if self.behavior else 400

            # Stream and deliver in sentence-sized chunks
            buf = ""
            total_sent = 0
            sent_any = False
            async for token in session.chat_stream(message):
                buf += token

                # Enforce max length
                if max_len > 0 and total_sent + len(buf) > max_len:
                    remaining = max_len - total_sent
                    if remaining > 0:
                        # Truncate at word boundary, not mid-word
                        snippet = buf[:remaining]
                        last_space = snippet.rfind(' ')
                        if last_space > remaining // 2:
                            snippet = snippet[:last_space]
                        self._send_chat(snippet.rstrip() + "…")
                        sent_any = True
                    break

                chunks = _chunk_sentences(buf, min_chunk=60)
                if len(chunks) > 1:
                    for chunk in chunks[:-1]:
                        self._send_chat(chunk)
                        total_sent += len(chunk)
                        sent_any = True
                        await asyncio.sleep(0.4)
                    buf = chunks[-1]

            # Send remaining buffer
            if buf.strip() and (max_len <= 0 or total_sent + len(buf) <= max_len):
                self._send_chat(buf.strip())
                sent_any = True

            if not sent_any:
                self._send_chat("(no response)")

            if self._mood_engine:
                self._mood_engine.record_interaction()

        except ImportError:
            log.warning("openai package not installed — bot chat unavailable")
            self._send_chat(
                "⚠ AI chat is not available right now. Please try again later."
            )
        except Exception as exc:
            log.exception("Bot chat handler error for %s", nick)
            err_msg = str(exc).lower()
            if "does not exist" in err_msg or "404" in err_msg:
                self._send_chat(
                    "⚠ The configured AI model was not found on this endpoint."
                )
            else:
                self._send_chat(
                    "⚠ Sorry, I ran into an issue processing that. Please try again."
                )
        finally:
            self._set_idle()

    async def _send_thinking_hints(self, nick: str, interval: int) -> None:
        """Periodically send "Thinking…" PMs while the LLM is processing."""
        try:
            # Wait the first interval before sending anything
            await asyncio.sleep(interval)
            dots = 1
            while True:
                hint = "Thinking" + "." * dots
                self._send_pm(nick, hint)
                dots = (dots % 3) + 1
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass  # normal — the LLM response came back

    async def _proactive_loop(self) -> None:
        """Periodically send a proactive message in main chat.

        The actual delay is *interval* ± 30 % (uniform) so the bot
        doesn't feel robotic.
        """
        import random

        interval = self.behavior.proactive_interval if self.behavior else 0
        prompts = self.behavior.proactive_prompts if self.behavior else []
        if not interval or not prompts:
            return

        try:
            while True:
                # ±30 % jitter so the bot feels more human
                jitter = random.uniform(-0.3, 0.3) * interval
                await asyncio.sleep(max(60, interval + jitter))

                prompt = random.choice(prompts)
                log.info("Bot proactive message: %s", prompt[:80])

                try:
                    self._set_thinking()

                    # Sample user count for mood while we're at it
                    if self._mood_engine:
                        try:
                            users = self.ctx.get_user_list()
                            count = len(users) if users else 0
                            self._mood_engine.sample_user_count(count)
                        except Exception:
                            pass

                    session = _get_or_create_session(
                        "chat:proactive",
                        self._bot_nick,
                        10,  # max class for system-initiated
                        self._bot_nick,
                        self._hub_name,
                        mode="chat",
                        llm_cfg=self.llm_cfg,
                        behavior=self.behavior,
                        mood_engine=self._mood_engine,
                        memory=self._memory,
                    )
                    response_text, _ = await session.chat(prompt)

                    max_len = self.behavior.max_chat_length if self.behavior else 400
                    if max_len > 0 and len(response_text) > max_len:
                        # Truncate at word boundary
                        snippet = response_text[:max_len]
                        last_space = snippet.rfind(' ')
                        if last_space > max_len // 2:
                            snippet = snippet[:last_space]
                        response_text = snippet.rstrip() + "…"

                    self._send_chat(response_text)
                except Exception:
                    log.exception("Proactive message failed")
                finally:
                    self._set_idle()
        except asyncio.CancelledError:
            pass

    async def _mood_sample_loop(self) -> None:
        """Periodically sample the hub user count for the mood engine."""
        try:
            while True:
                await asyncio.sleep(300)  # every 5 min
                try:
                    users = self.ctx.get_user_list()
                    count = len(users) if users else 0
                    if self._mood_engine:
                        self._mood_engine.sample_user_count(count)
                except Exception:
                    log.debug("Mood sample failed", exc_info=True)
        except asyncio.CancelledError:
            pass

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

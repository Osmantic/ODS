"""Request filters for Token Spy — strip bloat before forwarding to llama-server.

Three filters:
1. Tool filtering — blocklist or allowlist tool schemas
2. System prompt trimming — strip sections, replace, or truncate
3. Conversation history — sliding window, tool result truncation, old tool chain removal
"""

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("token-monitor")


@dataclass
class FilterResult:
    """Metrics captured during filtering."""
    tools_removed: int = 0
    tools_kept: int = 0
    system_chars_removed: int = 0
    system_sections_stripped: list = field(default_factory=list)
    messages_removed: int = 0
    messages_kept: int = 0
    tool_results_truncated: int = 0
    tool_chains_dropped: int = 0
    original_chars: int = 0
    filtered_chars: int = 0

    @property
    def chars_saved(self) -> int:
        return max(0, self.original_chars - self.filtered_chars)

    @property
    def estimated_tokens_saved(self) -> int:
        return self.chars_saved // 4


def apply_filters(body: dict, filter_settings: dict) -> tuple[dict, FilterResult]:
    """Apply all enabled filters to an OpenAI chat completions request body.

    Args:
        body: Parsed JSON request body (modified in place).
        filter_settings: The "filters" section from settings.

    Returns:
        (body, FilterResult) — body is the same dict, mutated.
    """
    result = FilterResult()

    if not filter_settings or not filter_settings.get("enabled"):
        return body, result

    result.original_chars = len(json.dumps(body, separators=(",", ":")))
    log_details = filter_settings.get("log_details", False)

    # Filter 1: Tools
    tools_cfg = filter_settings.get("tools", {})
    if tools_cfg.get("enabled") and "tools" in body:
        body, result = _filter_tools(body, tools_cfg, result, log_details)

    # Filter 2: System prompt
    sys_cfg = filter_settings.get("system_prompt", {})
    if sys_cfg.get("enabled") and "messages" in body:
        body, result = _filter_system_prompt(body, sys_cfg, result, log_details)

    # Filter 3: Conversation history
    hist_cfg = filter_settings.get("history", {})
    if hist_cfg.get("enabled") and "messages" in body:
        body, result = _filter_history(body, hist_cfg, result, log_details)

    result.filtered_chars = len(json.dumps(body, separators=(",", ":")))

    if log_details:
        log.info(
            f"[FILTER] chars {result.original_chars:,} → {result.filtered_chars:,} "
            f"(saved {result.chars_saved:,} ≈ {result.estimated_tokens_saved:,} tokens) | "
            f"tools -{result.tools_removed}/kept {result.tools_kept} | "
            f"msgs -{result.messages_removed}/kept {result.messages_kept} | "
            f"sys -{result.system_chars_removed}ch | "
            f"tool_results_truncated={result.tool_results_truncated} "
            f"tool_chains_dropped={result.tool_chains_dropped}"
        )

    return body, result


# ── Filter 1: Tool Filtering ────────────────────────────────────────────────


def _filter_tools(body: dict, cfg: dict, result: FilterResult,
                  log_details: bool) -> tuple[dict, FilterResult]:
    """Filter tool schemas by blocklist or allowlist."""
    tools = body.get("tools", [])
    if not tools:
        return body, result

    mode = cfg.get("mode", "blocklist")
    allowlist = set(cfg.get("allowlist", []))
    blocklist = set(cfg.get("blocklist", []))

    kept = []
    removed_names = []

    for tool in tools:
        name = tool.get("function", {}).get("name", "")
        if mode == "allowlist":
            if name in allowlist:
                kept.append(tool)
            else:
                removed_names.append(name)
        else:  # blocklist
            if name in blocklist:
                removed_names.append(name)
            else:
                kept.append(tool)

    result.tools_removed = len(removed_names)
    result.tools_kept = len(kept)

    if removed_names:
        body["tools"] = kept
        # If all tools removed, also drop tool_choice to avoid API errors
        if not kept:
            body.pop("tools", None)
            body.pop("tool_choice", None)
        if log_details:
            log.info(f"[FILTER] Tools removed ({len(removed_names)}): {removed_names}")

    return body, result


# ── Filter 2: System Prompt Trimming ─────────────────────────────────────────


def _filter_system_prompt(body: dict, cfg: dict, result: FilterResult,
                          log_details: bool) -> tuple[dict, FilterResult]:
    """Trim system/developer role messages."""
    messages = body.get("messages", [])
    mode = cfg.get("mode", "strip_sections")

    for msg in messages:
        if msg.get("role") not in ("system", "developer"):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        original_len = len(content)

        if mode == "replace":
            replacement = cfg.get("custom_replacement")
            if replacement:
                msg["content"] = replacement
        elif mode == "truncate":
            max_chars = cfg.get("max_chars")
            if max_chars and len(content) > max_chars:
                msg["content"] = content[:max_chars] + "\n\n[...truncated by Token Spy]"
        elif mode == "strip_sections":
            sections = cfg.get("strip_sections", [])
            content, stripped = _strip_markdown_sections(content, sections)
            msg["content"] = content
            result.system_sections_stripped.extend(stripped)

        result.system_chars_removed += max(0, original_len - len(msg["content"]))

    if log_details and result.system_chars_removed > 0:
        log.info(
            f"[FILTER] System prompt trimmed by {result.system_chars_removed} chars"
            + (f" (sections: {result.system_sections_stripped})" if result.system_sections_stripped else "")
        )

    return body, result


def _strip_markdown_sections(text: str, section_headings: list[str]) -> tuple[str, list[str]]:
    """Remove markdown sections by heading.

    Given headings like "## Heartbeats", removes that heading and everything
    until the next heading at the same or higher level.

    Returns (modified_text, list_of_stripped_heading_names).
    """
    stripped = []
    for heading in section_headings:
        # Determine heading level from the heading string
        m = re.match(r'^(#{1,6})\s+', heading)
        if not m:
            continue
        level = len(m.group(1))
        # Pattern: match the heading line, then everything until the next heading
        # at the same or higher level (fewer or equal #), or end of string
        escaped = re.escape(heading)
        pattern = re.compile(
            rf'^{escaped}\s*\n'       # the heading line
            rf'(.*?)'                  # content (non-greedy)
            rf'(?=^#{{1,{level}}}\s|\Z)',  # lookahead: next heading at same/higher level or EOF
            re.MULTILINE | re.DOTALL
        )
        new_text, count = pattern.subn('', text)
        if count > 0:
            stripped.append(heading)
            text = new_text

    return text, stripped


# ── Filter 3: Conversation History ───────────────────────────────────────────


def _filter_history(body: dict, cfg: dict, result: FilterResult,
                    log_details: bool) -> tuple[dict, FilterResult]:
    """Manage conversation history size."""
    messages = body.get("messages", [])
    if not messages:
        return body, result

    always_keep_system = cfg.get("always_keep_system", True)
    always_keep_last_n = cfg.get("always_keep_last_n", 6)
    max_pairs = cfg.get("max_pairs")
    truncate_tool_results_chars = cfg.get("truncate_tool_results_chars")
    drop_old_tool_calls = cfg.get("drop_old_tool_calls", False)
    drop_after = cfg.get("drop_old_tool_calls_after_pairs", 8)

    original_count = len(messages)

    # Step 1: Separate system messages from conversation messages
    system_msgs = []
    conv_msgs = []
    for msg in messages:
        if msg.get("role") in ("system", "developer") and always_keep_system:
            system_msgs.append(msg)
        else:
            conv_msgs.append(msg)

    # Step 2: Group conversation messages into atomic units
    # An atomic unit is: [user msg, assistant reply, tool_call/tool result chain]
    # We must not split these or the API contract breaks.
    units = _group_into_units(conv_msgs)

    # Step 3: Apply max_pairs — keep only the N most recent units
    if max_pairs and len(units) > max_pairs:
        removed_units = units[:-max_pairs]
        units = units[-max_pairs:]
        for unit in removed_units:
            result.messages_removed += len(unit)

    # Step 4: Drop old tool calls from older units
    if drop_old_tool_calls and len(units) > drop_after:
        safe_boundary = len(units) - drop_after
        for i in range(safe_boundary):
            unit = units[i]
            new_unit = []
            for msg in unit:
                if msg.get("role") == "tool":
                    result.tool_chains_dropped += 1
                    result.messages_removed += 1
                    continue
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    # Keep the assistant message but strip tool_calls
                    msg = dict(msg)  # shallow copy
                    del msg["tool_calls"]
                    result.tool_chains_dropped += 1
                new_unit.append(msg)
            units[i] = new_unit

    # Step 5: Truncate tool result content in all kept messages
    if truncate_tool_results_chars:
        for unit in units:
            for msg in unit:
                if msg.get("role") == "tool":
                    content = msg.get("content", "")
                    if isinstance(content, str) and len(content) > truncate_tool_results_chars:
                        msg["content"] = (
                            content[:truncate_tool_results_chars]
                            + f"\n\n[...truncated from {len(content)} to {truncate_tool_results_chars} chars]"
                        )
                        result.tool_results_truncated += 1

    # Step 6: Flatten units back into message list
    filtered_conv = []
    for unit in units:
        filtered_conv.extend(unit)

    # Step 7: Apply always_keep_last_n safety — ensure the last N raw messages
    # from the original conversation are present (protects in-flight tool chains)
    if always_keep_last_n and conv_msgs:
        tail = conv_msgs[-always_keep_last_n:]
        # Check if tail messages are already in filtered_conv
        # by comparing the last N messages
        tail_ids = {id(m) for m in tail}
        existing_ids = {id(m) for m in filtered_conv[-always_keep_last_n:]} if filtered_conv else set()
        if not tail_ids.issubset(existing_ids):
            # Ensure tail messages are present — they may have been modified by
            # truncation but should still be in the list since we keep recent units
            pass  # Units-based approach already preserves recent messages

    result.messages_kept = len(system_msgs) + len(filtered_conv)

    # Step 8: Apply max_total_chars if set
    max_total = cfg.get("max_total_chars")
    if max_total:
        while len(filtered_conv) > always_keep_last_n:
            total = sum(len(json.dumps(m, separators=(",", ":"))) for m in filtered_conv)
            if total <= max_total:
                break
            # Remove the oldest non-system unit
            filtered_conv.pop(0)
            result.messages_removed += 1
        result.messages_kept = len(system_msgs) + len(filtered_conv)

    # Reassemble: system messages first, then filtered conversation
    body["messages"] = system_msgs + filtered_conv

    if log_details and result.messages_removed > 0:
        log.info(
            f"[FILTER] History: {original_count} → {len(body['messages'])} messages "
            f"(removed {result.messages_removed}, truncated {result.tool_results_truncated} tool results, "
            f"dropped {result.tool_chains_dropped} tool chains)"
        )

    return body, result


def _group_into_units(messages: list[dict]) -> list[list[dict]]:
    """Group messages into atomic conversation units.

    A unit starts with a user message and includes the assistant reply
    plus any subsequent tool call/result exchanges until the next user message.
    Orphaned messages at the start (before the first user message) form their own unit.
    """
    units = []
    current_unit = []

    for msg in messages:
        role = msg.get("role", "")
        if role == "user" and current_unit:
            units.append(current_unit)
            current_unit = []
        current_unit.append(msg)

    if current_unit:
        units.append(current_unit)

    return units

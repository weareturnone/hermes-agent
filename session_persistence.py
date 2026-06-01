"""Helpers for making persisted session history safe to replay."""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Persisted transcripts are replayed into future model context. Large tool
# outputs can otherwise bloat session history before compression can run.
DEFAULT_PERSISTED_TOOL_OUTPUT_MAX_CHARS = 64 * 1024


def _persisted_tool_output_max_chars() -> int:
    raw = os.getenv("HERMES_PERSISTED_TOOL_OUTPUT_MAX_CHARS")
    if not raw:
        return DEFAULT_PERSISTED_TOOL_OUTPUT_MAX_CHARS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid HERMES_PERSISTED_TOOL_OUTPUT_MAX_CHARS=%r; using %d",
            raw, DEFAULT_PERSISTED_TOOL_OUTPUT_MAX_CHARS,
        )
        return DEFAULT_PERSISTED_TOOL_OUTPUT_MAX_CHARS
    return max(1024, value)


def _content_char_size(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    try:
        return len(json.dumps(content, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(content))


def _infer_tool_name(message: Dict[str, Any]) -> Optional[str]:
    tool_name = message.get("tool_name") or message.get("name")
    if tool_name:
        return str(tool_name)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and len(tool_calls) == 1:
        call = tool_calls[0]
        if isinstance(call, dict):
            function = call.get("function")
            if isinstance(function, dict) and function.get("name"):
                return str(function["name"])
            if call.get("name"):
                return str(call["name"])
    return None


def _compact_tool_content_placeholder(
    *,
    original_size: int,
    threshold: int,
    tool_name: Optional[str],
    tool_call_id: Optional[str],
    timestamp: Optional[str],
) -> str:
    metadata = {
        "type": "hermes_compacted_tool_result",
        "reason": "tool result omitted from persisted session history because it exceeded the configured size threshold",
        "original_char_size": original_size,
        "threshold_char_size": threshold,
    }
    if tool_name:
        metadata["tool_name"] = tool_name
    if tool_call_id:
        metadata["tool_call_id"] = tool_call_id
    if timestamp:
        metadata["timestamp"] = timestamp
    return "[Hermes compacted persisted tool result]\n" + json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
    )


def compact_large_tool_result_for_persistence(
    message: Dict[str, Any],
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a copy of *message* with oversized tool output replaced."""
    if not isinstance(message, dict):
        return message

    role = message.get("role")
    threshold = _persisted_tool_output_max_chars()
    tool_name = _infer_tool_name(message)
    tool_call_id = message.get("tool_call_id")
    timestamp = message.get("timestamp") or datetime.now().isoformat()

    if role in {"tool", "function"}:
        original_size = _content_char_size(message.get("content"))
        if original_size <= threshold:
            return message
        compacted = dict(message)
        compacted["content"] = _compact_tool_content_placeholder(
            original_size=original_size,
            threshold=threshold,
            tool_name=tool_name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
            timestamp=str(timestamp) if timestamp else None,
        )
        logger.info(
            "Compacted persisted tool result: session_id=%s role=%s tool_name=%s "
            "tool_call_id=%s original_chars=%d threshold=%d",
            session_id, role, tool_name, tool_call_id, original_size, threshold,
        )
        return compacted

    content = message.get("content")
    if not isinstance(content, list):
        return message

    changed = False
    compacted_blocks = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            compacted_blocks.append(block)
            continue
        block_content = block.get("content")
        original_size = _content_char_size(block_content)
        if original_size <= threshold:
            compacted_blocks.append(block)
            continue
        compacted_block = dict(block)
        compacted_block["content"] = _compact_tool_content_placeholder(
            original_size=original_size,
            threshold=threshold,
            tool_name=tool_name,
            tool_call_id=str(tool_call_id or block.get("tool_use_id") or "") or None,
            timestamp=str(timestamp) if timestamp else None,
        )
        compacted_blocks.append(compacted_block)
        changed = True
        logger.info(
            "Compacted persisted tool_result content block: session_id=%s "
            "tool_name=%s tool_call_id=%s original_chars=%d threshold=%d",
            session_id, tool_name, tool_call_id or block.get("tool_use_id"),
            original_size, threshold,
        )

    if not changed:
        return message
    compacted = dict(message)
    compacted["content"] = compacted_blocks
    return compacted

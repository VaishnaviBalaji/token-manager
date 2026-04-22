"""
Optimisation layer: prompt compression + model routing.

Philosophy: never silently change what the user sends.
All optimisations are opt-in and surfaced transparently.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Model routing thresholds
# ------------------------------------------------------------------

ROUTING_TABLE = [
    # (max_estimated_tokens, recommended_model)
    (2_000,  "claude-haiku-4-5-20251001"),
    (10_000, "claude-sonnet-4-6"),
    (None,   "claude-opus-4-6"),       # None = no upper bound
]


def suggest_model(estimated_input_tokens: int) -> str:
    """
    Route to the cheapest model that comfortably handles the prompt size.
    Override in your own ROUTING_TABLE as needed.
    """
    for limit, model in ROUTING_TABLE:
        if limit is None or estimated_input_tokens <= limit:
            return model
    return "claude-sonnet-4-6"


# ------------------------------------------------------------------
# Prompt compression
# ------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """
    Rough token estimator (~4 chars per token for English).
    Good enough for routing decisions — not a replacement for tiktoken/cl100k.
    """
    return max(1, len(text) // 4)


def compress_whitespace(text: str) -> str:
    """Collapse redundant whitespace and blank lines."""
    text = re.sub(r"\n{3,}", "\n\n", text)   # max 2 consecutive newlines
    text = re.sub(r" {2,}", " ", text)        # collapse spaces
    return text.strip()


def truncate_to_budget(text: str, max_tokens: int, from_end: bool = False) -> str:
    """
    Hard-truncate text to fit within max_tokens (estimated).
    from_end=True keeps the tail of the text (useful for conversation history).
    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    if from_end:
        return "...[truncated]...\n" + text[-max_chars:]
    return text[:max_chars] + "\n...[truncated]..."


def compress_prompt(
    system: Optional[str],
    user_message: str,
    max_prompt_tokens: Optional[int] = None,
    aggressive: bool = False,
) -> dict:
    """
    Apply a compression pipeline to system + user message.

    Returns:
        {
            "system": str | None,
            "user_message": str,
            "original_estimated_tokens": int,
            "compressed_estimated_tokens": int,
            "savings_pct": float,
        }
    """
    original = (system or "") + user_message
    original_tokens = estimate_tokens(original)

    # Step 1: whitespace compression (always safe)
    if system:
        system = compress_whitespace(system)
    user_message = compress_whitespace(user_message)

    # Step 2: aggressive truncation (opt-in)
    if aggressive and max_prompt_tokens:
        # Give system prompt 40% of budget, user message 60%
        sys_budget  = int(max_prompt_tokens * 0.4)
        user_budget = int(max_prompt_tokens * 0.6)
        if system:
            system = truncate_to_budget(system, sys_budget)
        user_message = truncate_to_budget(user_message, user_budget, from_end=True)

    compressed = (system or "") + user_message
    compressed_tokens = estimate_tokens(compressed)
    savings = (1 - compressed_tokens / original_tokens) if original_tokens else 0

    if savings > 0.01:
        logger.info(
            "Prompt compressed: %d → %d estimated tokens (%.1f%% saved)",
            original_tokens, compressed_tokens, savings * 100,
        )

    return {
        "system": system,
        "user_message": user_message,
        "original_estimated_tokens": original_tokens,
        "compressed_estimated_tokens": compressed_tokens,
        "savings_pct": round(savings, 4),
    }

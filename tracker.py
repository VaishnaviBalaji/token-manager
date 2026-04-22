"""
TokenTracker: transparent wrapper around the Anthropic messages API.

Usage:
    from token_manager import TokenTracker, BudgetConfig

    tracker = TokenTracker(session_id="my-app")
    tracker.set_budget(BudgetConfig(session_id="my-app", max_cost_usd=0.10))

    response = tracker.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hello!"}],
    )
    # response is the standard Anthropic Message object — nothing changed.
"""

import logging
import uuid
from typing import Optional, Any

import anthropic

from .models import CallRecord, TokenUsage, BudgetConfig, calculate_cost
from .budget import BudgetManager
from . import storage
from .optimizer import suggest_model, compress_prompt

logger = logging.getLogger(__name__)


class TokenTracker:
    def __init__(
        self,
        session_id: Optional[str] = None,
        agent_name: str = "default",
        api_key: Optional[str] = None,
        auto_route: bool = False,
        auto_compress: bool = False,
        raise_on_budget_exceeded: bool = True,
    ):
        """
        Args:
            session_id:               unique ID for grouping calls (defaults to a UUID)
            agent_name:               label for this agent/component
            api_key:                  Anthropic API key (falls back to ANTHROPIC_API_KEY env var)
            auto_route:               automatically pick cheapest model based on prompt size
            auto_compress:            apply whitespace compression before sending
            raise_on_budget_exceeded: raise BudgetExceededError if budget is blown
        """
        self.session_id  = session_id or str(uuid.uuid4())
        self.agent_name  = agent_name
        self.auto_route  = auto_route
        self.auto_compress = auto_compress
        self.raise_on_exceeded = raise_on_budget_exceeded

        self._client  = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._budget  = BudgetManager()

        storage.init_db()
        logger.info("TokenTracker initialised — session: %s", self.session_id)

    # ------------------------------------------------------------------
    # Budget helpers (pass-through to BudgetManager)
    # ------------------------------------------------------------------

    def set_budget(self, config: BudgetConfig) -> None:
        self._budget.set_budget(config)

    def check_budget(self):
        return self._budget.check(self.session_id, raise_on_exceeded=self.raise_on_exceeded)

    # ------------------------------------------------------------------
    # Main entry point — drop-in replacement for client.messages.create()
    # ------------------------------------------------------------------

    def create(self, model: str, messages: list[dict], **kwargs) -> Any:
        """
        Wraps anthropic.messages.create().
        Tracks usage, checks budget, applies optimisations if enabled.
        Returns the original Anthropic Message object unchanged.
        """
        # 1. Pre-call budget check (guard before spending more tokens)
        self._budget.check(self.session_id, raise_on_exceeded=self.raise_on_exceeded)

        # 2. Optional: auto-compress the last user message
        if self.auto_compress and messages:
            messages = self._maybe_compress(messages, kwargs.get("system"))

        # 3. Optional: auto-route to cheapest model
        if self.auto_route:
            suggested = self._suggest_model(messages, kwargs.get("system", ""))
            if suggested != model:
                logger.info("Auto-routing: %s → %s", model, suggested)
                model = suggested

        # 4. API call
        response = self._client.messages.create(model=model, messages=messages, **kwargs)

        # 5. Extract usage
        usage       = response.usage
        input_tok   = usage.input_tokens
        output_tok  = usage.output_tokens
        total_tok   = input_tok + output_tok
        cost        = calculate_cost(model, input_tok, output_tok)

        token_usage = TokenUsage(
            input_tokens=input_tok,
            output_tokens=output_tok,
            total_tokens=total_tok,
            cost_usd=cost,
        )

        # 6. Persist
        prompt_preview = self._get_preview(messages)
        record = CallRecord(
            session_id=self.session_id,
            agent_name=self.agent_name,
            model=model,
            input_tokens=input_tok,
            output_tokens=output_tok,
            total_tokens=total_tok,
            cost_usd=cost,
            prompt_preview=prompt_preview,
        )
        storage.insert_record(record)

        logger.info(
            "Call logged — model: %s | in: %d | out: %d | cost: $%.6f",
            model, input_tok, output_tok, cost,
        )

        # 7. Post-call budget check
        self._budget.check(self.session_id, raise_on_exceeded=self.raise_on_exceeded)

        return response

    # ------------------------------------------------------------------
    # Session reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        totals = storage.get_session_totals(self.session_id)
        budget = storage.get_budget(self.session_id)
        return {"session_id": self.session_id, "usage": totals, "budget": budget}

    def history(self) -> list[dict]:
        return storage.get_session_records(self.session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_preview(self, messages: list[dict]) -> str:
        for msg in reversed(messages):
            content = msg.get("content", "")
            if isinstance(content, str):
                return content[:100]
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block["text"][:100]
        return ""

    def _suggest_model(self, messages: list[dict], system: str) -> str:
        text = system + " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages
        )
        from .optimizer import estimate_tokens
        return suggest_model(estimate_tokens(text))

    def _maybe_compress(self, messages: list[dict], system: Optional[str]) -> list[dict]:
        if not messages:
            return messages
        last = messages[-1]
        content = last.get("content", "")
        if not isinstance(content, str):
            return messages
        result = compress_prompt(system, content)
        messages = messages[:-1] + [{**last, "content": result["user_message"]}]
        return messages

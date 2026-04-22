import json
import logging
import threading
import urllib.request
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
        tm_key: Optional[str] = None,
        ingest_url: str = "http://127.0.0.1:8002/ingest",
    ):
        """
        Args:
            session_id:               unique ID for grouping calls (defaults to a UUID)
            agent_name:               label for this agent/component
            api_key:                  Anthropic API key (falls back to ANTHROPIC_API_KEY env var)
            auto_route:               automatically pick cheapest model based on prompt size
            auto_compress:            apply whitespace compression before sending
            raise_on_budget_exceeded: raise BudgetExceededError if budget is blown
            tm_key:                   Token Manager API key (sk-tm-...) for cloud tracking
            ingest_url:               Token Manager server URL
        """
        self.session_id        = session_id or str(uuid.uuid4())
        self.agent_name        = agent_name
        self.auto_route        = auto_route
        self.auto_compress     = auto_compress
        self.raise_on_exceeded = raise_on_budget_exceeded
        self._tm_key           = tm_key
        self._ingest_url       = ingest_url

        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._budget = BudgetManager()

        storage.init_db()
        if tm_key:
            logger.info("TokenTracker cloud tracking enabled — session: %s", self.session_id)
        else:
            logger.info("TokenTracker local-only — session: %s", self.session_id)

    # ------------------------------------------------------------------
    # Budget helpers
    # ------------------------------------------------------------------

    def set_budget(self, config: BudgetConfig) -> None:
        self._budget.set_budget(config)

    def check_budget(self):
        return self._budget.check(self.session_id, raise_on_exceeded=self.raise_on_exceeded)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def create(self, model: str, messages: list[dict], **kwargs) -> Any:
        self._budget.check(self.session_id, raise_on_exceeded=self.raise_on_exceeded)

        if self.auto_compress and messages:
            messages = self._maybe_compress(messages, kwargs.get("system"))

        if self.auto_route:
            suggested = self._suggest_model(messages, kwargs.get("system", ""))
            if suggested != model:
                logger.info("Auto-routing: %s → %s", model, suggested)
                model = suggested

        response = self._client.messages.create(model=model, messages=messages, **kwargs)

        input_tok  = response.usage.input_tokens
        output_tok = response.usage.output_tokens
        cost       = calculate_cost(model, input_tok, output_tok)
        preview    = self._get_preview(messages)

        # Local SQLite record
        record = CallRecord(
            session_id=self.session_id,
            agent_name=self.agent_name,
            model=model,
            input_tokens=input_tok,
            output_tokens=output_tok,
            total_tokens=input_tok + output_tok,
            cost_usd=cost,
            prompt_preview=preview,
        )
        storage.insert_record(record)

        # Cloud ingest — non-blocking background thread
        if self._tm_key:
            self._ingest_async(model, input_tok, output_tok, cost, preview)

        logger.info("Call logged — model: %s | in: %d | out: %d | $%.6f",
                    model, input_tok, output_tok, cost)

        self._budget.check(self.session_id, raise_on_exceeded=self.raise_on_exceeded)
        return response

    # ------------------------------------------------------------------
    # Reporting
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

    def _ingest_async(self, model: str, input_tok: int, output_tok: int,
                      cost: float, preview: Optional[str]) -> None:
        payload = {
            "tm_api_key":    self._tm_key,
            "session_id":    self.session_id,
            "agent_name":    self.agent_name,
            "model":         model,
            "input_tokens":  input_tok,
            "output_tokens": output_tok,
            "cost_usd":      cost,
            "prompt_preview": preview,
        }

        def _send():
            try:
                data = json.dumps(payload).encode()
                req  = urllib.request.Request(
                    self._ingest_url, data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as exc:
                logger.warning("Cloud ingest failed (non-fatal): %s", exc)

        t = threading.Thread(target=_send, daemon=False)
        t.start()

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
        last    = messages[-1]
        content = last.get("content", "")
        if not isinstance(content, str):
            return messages
        result   = compress_prompt(system, content)
        messages = messages[:-1] + [{**last, "content": result["user_message"]}]
        return messages

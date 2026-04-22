import logging
from typing import Optional, Callable
from .models import BudgetConfig, BudgetStatus
from . import storage

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when a session has exceeded its token or cost budget."""
    pass


class BudgetManager:
    def __init__(
        self,
        on_alert: Optional[Callable[[BudgetStatus], None]] = None,
        on_exceeded: Optional[Callable[[BudgetStatus], None]] = None,
    ):
        """
        Args:
            on_alert:    callback fired when usage crosses alert_threshold
            on_exceeded: callback fired when budget is exceeded (before raising)
        """
        self._on_alert = on_alert or self._default_alert
        self._on_exceeded = on_exceeded or self._default_exceeded

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_budget(self, config: BudgetConfig) -> None:
        storage.upsert_budget(
            session_id=config.session_id,
            max_tokens=config.max_tokens,
            max_cost_usd=config.max_cost_usd,
            alert_threshold=config.alert_threshold,
        )
        logger.info(
            "Budget set for session '%s': max_tokens=%s, max_cost=$%s",
            config.session_id, config.max_tokens, config.max_cost_usd,
        )

    def check(self, session_id: str, raise_on_exceeded: bool = True) -> BudgetStatus:
        """
        Evaluate current usage against budget for a session.
        Fires callbacks and optionally raises BudgetExceededError.
        """
        budget = storage.get_budget(session_id)
        totals = storage.get_session_totals(session_id)

        tokens_used = totals["total_tokens"]
        cost_used   = totals["cost_usd"]

        max_tokens   = budget["max_tokens"]   if budget else None
        max_cost     = budget["max_cost_usd"] if budget else None
        threshold    = budget["alert_threshold"] if budget else 0.8

        token_pct = (tokens_used / max_tokens) if max_tokens else None
        cost_pct  = (cost_used / max_cost)     if max_cost   else None

        budget_exceeded = bool(
            (max_tokens and tokens_used >= max_tokens) or
            (max_cost   and cost_used   >= max_cost)
        )
        alert_triggered = not budget_exceeded and bool(
            (token_pct and token_pct >= threshold) or
            (cost_pct  and cost_pct  >= threshold)
        )

        status = BudgetStatus(
            session_id=session_id,
            tokens_used=tokens_used,
            cost_used_usd=cost_used,
            tokens_limit=max_tokens,
            cost_limit_usd=max_cost,
            token_pct=round(token_pct, 4) if token_pct else None,
            cost_pct=round(cost_pct, 4)   if cost_pct  else None,
            alert_triggered=alert_triggered,
            budget_exceeded=budget_exceeded,
        )

        if alert_triggered:
            self._on_alert(status)
        if budget_exceeded:
            self._on_exceeded(status)
            if raise_on_exceeded:
                raise BudgetExceededError(
                    f"Session '{session_id}' has exceeded its budget. "
                    f"Tokens used: {tokens_used}, Cost: ${cost_used:.6f}"
                )

        return status

    # ------------------------------------------------------------------
    # Default callbacks
    # ------------------------------------------------------------------

    @staticmethod
    def _default_alert(status: BudgetStatus) -> None:
        pct = max(
            filter(None, [status.token_pct, status.cost_pct]), default=0
        )
        logger.warning(
            "⚠️  BUDGET ALERT — session '%s': %.0f%% of budget used "
            "(tokens: %d, cost: $%.6f)",
            status.session_id, pct * 100,
            status.tokens_used, status.cost_used_usd,
        )

    @staticmethod
    def _default_exceeded(status: BudgetStatus) -> None:
        logger.error(
            "🚨 BUDGET EXCEEDED — session '%s': tokens=%d, cost=$%.6f",
            status.session_id, status.tokens_used, status.cost_used_usd,
        )

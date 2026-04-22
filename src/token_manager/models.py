from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


# Pricing per million tokens (USD) — update as Anthropic changes rates
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.25, "output": 1.25},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
}

DEFAULT_PRICING = {"input": 3.00, "output": 15.00}  # fallback to Sonnet pricing


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float


class CallRecord(BaseModel):
    id: Optional[int] = None
    session_id: str
    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    prompt_preview: Optional[str] = None  # first 100 chars for debugging


class BudgetConfig(BaseModel):
    session_id: str
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    alert_threshold: float = 0.8  # alert at 80% of budget


class BudgetStatus(BaseModel):
    session_id: str
    tokens_used: int
    cost_used_usd: float
    tokens_limit: Optional[int]
    cost_limit_usd: Optional[float]
    token_pct: Optional[float]
    cost_pct: Optional[float]
    alert_triggered: bool
    budget_exceeded: bool


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    cost = (input_tokens / 1_000_000) * pricing["input"]
    cost += (output_tokens / 1_000_000) * pricing["output"]
    return round(cost, 8)

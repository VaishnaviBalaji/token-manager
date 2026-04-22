"""
Token Manager — FastAPI dashboard

Run locally:
    uvicorn api.main:app --reload

Endpoints:
    GET  /sessions                    — all sessions summary
    GET  /sessions/{session_id}       — session detail + history
    GET  /sessions/{session_id}/budget — budget status
    POST /sessions/{session_id}/budget — set budget
    GET  /health
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from src.token_manager import storage, BudgetConfig
from src.token_manager.budget import BudgetManager
from src.token_manager.models import BudgetStatus

app = FastAPI(
    title="Token Manager",
    description="Token usage tracking and budget management for Anthropic API calls",
    version="0.1.0",
)

storage.init_db()
_budget_manager = BudgetManager()


# ------------------------------------------------------------------
# Request / response schemas
# ------------------------------------------------------------------

class BudgetRequest(BaseModel):
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    alert_threshold: float = 0.8


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sessions")
def list_sessions():
    return storage.get_all_sessions_summary()


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    totals  = storage.get_session_totals(session_id)
    records = storage.get_session_records(session_id)
    budget  = storage.get_budget(session_id)
    return {
        "session_id": session_id,
        "totals": totals,
        "budget": budget,
        "calls": records,
    }


@app.get("/sessions/{session_id}/budget", response_model=BudgetStatus)
def get_budget_status(session_id: str):
    try:
        return _budget_manager.check(session_id, raise_on_exceeded=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sessions/{session_id}/budget")
def set_budget(session_id: str, body: BudgetRequest):
    config = BudgetConfig(
        session_id=session_id,
        max_tokens=body.max_tokens,
        max_cost_usd=body.max_cost_usd,
        alert_threshold=body.alert_threshold,
    )
    _budget_manager.set_budget(config)
    return {"status": "budget set", "session_id": session_id}

"""
Unit tests — no Anthropic API calls required.
"""
import pytest
from pathlib import Path
from src.token_manager.models import calculate_cost, BudgetConfig
from src.token_manager import storage
from src.token_manager.budget import BudgetManager, BudgetExceededError
from src.token_manager.optimizer import (
    estimate_tokens, compress_whitespace, truncate_to_budget, suggest_model
)

TEST_DB = Path("test_token_manager.db")


@pytest.fixture(autouse=True)
def clean_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    storage.init_db(TEST_DB)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------

def test_cost_calculation_sonnet():
    cost = calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert cost == pytest.approx(18.0)


def test_cost_calculation_haiku():
    cost = calculate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert cost == pytest.approx(1.50)


def test_cost_calculation_unknown_model():
    # Falls back to Sonnet pricing
    cost = calculate_cost("unknown-model", 1_000_000, 0)
    assert cost == pytest.approx(3.0)


# ------------------------------------------------------------------
# Storage
# ------------------------------------------------------------------

def test_insert_and_retrieve(tmp_path):
    db = tmp_path / "test.db"
    storage.init_db(db)
    from src.token_manager.models import CallRecord
    from datetime import datetime

    record = CallRecord(
        session_id="sess-1",
        agent_name="test-agent",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        cost_usd=0.001,
        timestamp=datetime.utcnow(),
        prompt_preview="test prompt",
    )
    storage.insert_record(record, db)

    totals = storage.get_session_totals("sess-1", db)
    assert totals["total_tokens"] == 150
    assert totals["call_count"] == 1


def test_session_totals_empty(tmp_path):
    db = tmp_path / "test.db"
    storage.init_db(db)
    totals = storage.get_session_totals("nonexistent", db)
    assert totals["total_tokens"] == 0
    assert totals["cost_usd"] == 0.0


# ------------------------------------------------------------------
# Budget
# ------------------------------------------------------------------

def test_budget_alert_fires(tmp_path, caplog):
    import logging
    import src.token_manager.storage as smod

    db = tmp_path / "test.db"
    storage.init_db(db)
    storage.upsert_budget("sess-alert", max_tokens=1000, max_cost_usd=None,
                          alert_threshold=0.8, path=db)

    from src.token_manager.models import CallRecord
    from datetime import datetime
    record = CallRecord(
        session_id="sess-alert", agent_name="test", model="claude-sonnet-4-6",
        input_tokens=850, output_tokens=0, total_tokens=850,
        cost_usd=0.001, timestamp=datetime.utcnow(),
    )
    storage.insert_record(record, db)

    orig_budget = smod.get_budget
    orig_totals = smod.get_session_totals
    smod.get_budget = lambda sid, path=db: orig_budget(sid, path)
    smod.get_session_totals = lambda sid, path=db: orig_totals(sid, path)

    try:
        bm = BudgetManager()
        with caplog.at_level(logging.WARNING):
            status = bm.check("sess-alert", raise_on_exceeded=False)
    finally:
        smod.get_budget = orig_budget
        smod.get_session_totals = orig_totals

    assert status.alert_triggered is True
    assert status.budget_exceeded is False


def test_budget_exceeded_raises(tmp_path):
    import src.token_manager.storage as smod

    db = tmp_path / "test.db"
    storage.init_db(db)
    storage.upsert_budget("sess-over", max_tokens=100, max_cost_usd=None,
                          alert_threshold=0.8, path=db)

    from src.token_manager.models import CallRecord
    from datetime import datetime
    record = CallRecord(
        session_id="sess-over", agent_name="test", model="claude-sonnet-4-6",
        input_tokens=200, output_tokens=0, total_tokens=200,
        cost_usd=0.001, timestamp=datetime.utcnow(),
    )
    storage.insert_record(record, db)

    orig_budget = smod.get_budget
    orig_totals = smod.get_session_totals
    smod.get_budget = lambda sid, path=db: orig_budget(sid, path)
    smod.get_session_totals = lambda sid, path=db: orig_totals(sid, path)

    try:
        bm = BudgetManager()
        with pytest.raises(BudgetExceededError):
            bm.check("sess-over", raise_on_exceeded=True)
    finally:
        smod.get_budget = orig_budget
        smod.get_session_totals = orig_totals


# ------------------------------------------------------------------
# Optimiser
# ------------------------------------------------------------------

def test_estimate_tokens():
    assert estimate_tokens("hello world") == 2


def test_compress_whitespace():
    messy = "Hello    world\n\n\n\nFoo"
    cleaned = compress_whitespace(messy)
    assert "    " not in cleaned
    assert "\n\n\n" not in cleaned


def test_truncate_to_budget():
    long_text = "a" * 1000
    truncated = truncate_to_budget(long_text, max_tokens=10)
    assert len(truncated) < len(long_text)


def test_suggest_model_routing():
    assert suggest_model(500) == "claude-haiku-4-5-20251001"
    assert suggest_model(5_000) == "claude-sonnet-4-6"
    assert suggest_model(50_000) == "claude-opus-4-6"

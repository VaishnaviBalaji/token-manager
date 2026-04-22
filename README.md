# Token Manager

> Tokens are the new currency of intelligence.

A lightweight Python SDK + FastAPI dashboard for tracking, budgeting, and optimising token usage across Anthropic API calls and agent pipelines.

---

## Features

| Feature | Status |
|---|---|
| Token tracking per session/agent | ✅ |
| Cost calculation (per model) | ✅ |
| Budget limits with alerts | ✅ |
| Prompt compression | ✅ |
| Model auto-routing (cheapest fit) | ✅ |
| FastAPI dashboard | ✅ |
| SQLite backend (swap to BigQuery) | ✅ |

---

## Quickstart

```bash
pip install -e ".[dev]"
```

```python
from src.token_manager import TokenTracker, BudgetConfig

tracker = TokenTracker(session_id="my-app", agent_name="chatbot")

# Optional: set a budget
tracker.set_budget(BudgetConfig(
    session_id="my-app",
    max_cost_usd=0.10,
    alert_threshold=0.8,
))

# Drop-in replacement for anthropic.messages.create()
response = tracker.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)

# Check usage anytime
print(tracker.summary())
```

## Auto-optimisation

```python
tracker = TokenTracker(
    session_id="optimised-app",
    auto_route=True,      # picks cheapest model for prompt size
    auto_compress=True,   # strips whitespace before sending
)
```

## Run the dashboard

```bash
uvicorn api.main:app --reload
# → http://localhost:8000/docs
```

## Run tests

```bash
pytest
```

## Docker

```bash
docker build -t token-manager .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=your_key token-manager
```

---

## Architecture

```
token-manager/
├── src/token_manager/
│   ├── tracker.py      # Core wrapper (entry point)
│   ├── storage.py      # SQLite persistence
│   ├── budget.py       # Budget limits + alerts
│   ├── optimizer.py    # Compression + model routing
│   └── models.py       # Pydantic schemas + pricing
├── api/main.py         # FastAPI dashboard
├── tests/
├── Dockerfile
└── pyproject.toml
```

## Roadmap

- [ ] BigQuery backend option
- [ ] Streaming token support
- [ ] Per-agent budget isolation in multi-agent pipelines
- [ ] Web UI dashboard (React)
- [ ] LangChain callback integration

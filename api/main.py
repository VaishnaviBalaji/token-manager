from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional

from src.token_manager import storage, BudgetConfig
from src.token_manager.budget import BudgetManager
from src.token_manager.models import BudgetStatus, CallRecord
from src.token_manager import auth

app = FastAPI(title="Token Manager", version="0.1.0")

storage.init_db()
_budget_manager = BudgetManager()
_security = HTTPBearer()

DASHBOARD_HTML = Path(__file__).parent / "templates" / "dashboard.html"


# ------------------------------------------------------------------
# Auth helpers
# ------------------------------------------------------------------

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> dict:
    payload = auth.decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    user = storage.get_user_by_id(int(payload["sub"]))
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ------------------------------------------------------------------
# Request schemas
# ------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class BudgetRequest(BaseModel):
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    alert_threshold: float = 0.8

class IngestRequest(BaseModel):
    tm_api_key: str
    session_id: str
    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    prompt_preview: Optional[str] = None


# ------------------------------------------------------------------
# UI
# ------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(DASHBOARD_HTML)


# ------------------------------------------------------------------
# Auth endpoints
# ------------------------------------------------------------------

@app.post("/auth/register", status_code=201)
def register(body: RegisterRequest):
    if storage.get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")

    hashed = auth.hash_password(body.password)
    tm_key = auth.generate_tm_api_key()
    user_id = storage.create_user(body.email, hashed, tm_key)
    token = auth.create_access_token(user_id, body.email)

    return {"access_token": token, "token_type": "bearer",
            "tm_api_key": tm_key, "email": body.email}


@app.post("/auth/login")
def login(body: LoginRequest):
    user = storage.get_user_by_email(body.email)
    if not user or not auth.verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = auth.create_access_token(user["id"], user["email"])
    return {"access_token": token, "token_type": "bearer",
            "tm_api_key": user["tm_api_key"], "email": user["email"]}


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"],
            "tm_api_key": user["tm_api_key"], "created_at": user["created_at"]}


# ------------------------------------------------------------------
# SDK ingest — authenticated by TM API key (not JWT)
# ------------------------------------------------------------------

@app.post("/ingest", status_code=201)
def ingest(body: IngestRequest):
    user = storage.get_user_by_tm_key(body.tm_api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from datetime import datetime
    record = CallRecord(
        session_id=body.session_id,
        agent_name=body.agent_name,
        model=body.model,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        total_tokens=body.input_tokens + body.output_tokens,
        cost_usd=body.cost_usd,
        timestamp=datetime.utcnow(),
        prompt_preview=body.prompt_preview,
    )
    storage.insert_record(record, user_id=user["id"])
    return {"status": "ok"}


# ------------------------------------------------------------------
# Stats + seed (scoped to logged-in user)
# ------------------------------------------------------------------

@app.get("/stats")
def get_stats(user: dict = Depends(get_current_user)):
    return storage.get_stats(user_id=user["id"])


@app.post("/demo/seed")
def seed_demo(user: dict = Depends(get_current_user)):
    n = storage.seed_demo_data(user_id=user["id"])
    return {"seeded": n}


@app.delete("/data")
def clear_data(user: dict = Depends(get_current_user)):
    storage.clear_user_data(user_id=user["id"])
    return {"status": "cleared"}


# ------------------------------------------------------------------
# Sessions (scoped to logged-in user)
# ------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sessions")
def list_sessions(user: dict = Depends(get_current_user)):
    return storage.get_all_sessions_summary(user_id=user["id"])


@app.get("/sessions/{session_id}")
def get_session(session_id: str, user: dict = Depends(get_current_user)):
    totals  = storage.get_session_totals(session_id, user_id=user["id"])
    records = storage.get_session_records(session_id, user_id=user["id"])
    budget  = storage.get_budget(session_id, user_id=user["id"])
    return {"session_id": session_id, "totals": totals,
            "budget": budget, "calls": records}


@app.get("/sessions/{session_id}/budget", response_model=BudgetStatus)
def get_budget_status(session_id: str, user: dict = Depends(get_current_user)):
    try:
        return _budget_manager.check(session_id, raise_on_exceeded=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sessions/{session_id}/budget")
def set_budget(session_id: str, body: BudgetRequest,
               user: dict = Depends(get_current_user)):
    storage.upsert_budget(
        session_id=session_id,
        max_tokens=body.max_tokens,
        max_cost_usd=body.max_cost_usd,
        alert_threshold=body.alert_threshold,
        user_id=user["id"],
    )
    return {"status": "budget set", "session_id": session_id}

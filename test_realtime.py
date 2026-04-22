"""
Quick smoke-test: makes one real Anthropic API call and sends it to your dashboard.

Usage:
    python test_realtime.py <tm-key> <anthropic-api-key>
"""
import sys
import os
from src.token_manager import TokenTracker

if len(sys.argv) < 3:
    print("Usage: python test_realtime.py sk-tm-YOUR_TM_KEY sk-ant-YOUR_ANTHROPIC_KEY")
    sys.exit(1)

tm_key      = sys.argv[1]
anthropic_key = sys.argv[2]

tracker = TokenTracker(
    session_id="my-first-real-session",
    agent_name="smoke-test",
    api_key=anthropic_key,
    tm_key=tm_key,
    ingest_url="http://127.0.0.1:8002/ingest",
)

print("Making a real API call...")
response = tracker.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=50,
    messages=[{"role": "user", "content": "Say hello in exactly 5 words."}],
)

print("Response:", response.content[0].text)
print()
print(tracker.summary())
print()
print("Check your dashboard at http://127.0.0.1:8002 — the call should appear within 5 seconds.")

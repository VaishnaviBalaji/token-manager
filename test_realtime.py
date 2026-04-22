"""
Quick smoke-test — verifies real-time tracking is working.

Your Anthropic key is read from the ANTHROPIC_API_KEY environment variable.
Token Manager never sees your Anthropic key — only token counts and cost.

Usage:
    # Set your Anthropic key once in your environment:
    export ANTHROPIC_API_KEY=sk-ant-...        # Mac/Linux
    set ANTHROPIC_API_KEY=sk-ant-...           # Windows cmd
    $env:ANTHROPIC_API_KEY="sk-ant-..."        # Windows PowerShell

    # Then run (only your sk-tm- key goes here):
    python test_realtime.py sk-tm-YOUR_TM_KEY
"""
import sys
from src.token_manager import TokenTracker

if len(sys.argv) < 2:
    print("Usage: python test_realtime.py sk-tm-YOUR_TM_KEY")
    print("Set ANTHROPIC_API_KEY in your environment separately.")
    sys.exit(1)

tm_key = sys.argv[1]

tracker = TokenTracker(
    session_id="my-first-real-session",
    agent_name="smoke-test",
    tm_key=tm_key,
    ingest_url="https://token-manager-production-4f5b.up.railway.app/ingest",
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
print("Check your dashboard at https://token-manager-production-4f5b.up.railway.app")

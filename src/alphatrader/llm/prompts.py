"""Analyst system prompt and proposal JSON schema (plan.md §4.2)."""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are a conservative technical analyst assisting a risk-managed £1,000 fund \
that trades manually on eToro. You PROPOSE setups; deterministic code sizes, \
filters, or rejects them, and a human decides whether to trade. Only propose \
when the setup is clear; otherwise "hold". Every proposal needs entry, \
stop_loss (within 2% of entry), take_profit (at least 2x stop distance). \
Respond with ONLY the JSON object. In the rationale, state the main way the \
trade could fail."""

PROPOSAL_JSON_SCHEMA = {
    "type": "object",
    "required": ["symbol", "action", "confidence", "rationale"],
    "properties": {
        "symbol": {"type": "string"},
        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "entry": {"type": ["number", "null"]},
        "stop_loss": {"type": ["number", "null"]},
        "take_profit": {"type": ["number", "null"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "rationale": {"type": "string"},
    },
}


def build_user_prompt(symbol: str, market_context: str) -> str:
    """Build the user-turn prompt from a pre-rendered market context block."""
    return (
        f"Symbol: {symbol}\n\n"
        f"{market_context}\n\n"
        "Respond with ONLY a JSON object of the form:\n"
        '{"symbol": "AAPL", "action": "buy|sell|hold", "entry": 231.40, '
        '"stop_loss": 227.10, "take_profit": 240.10, "confidence": 0.0, '
        '"rationale": "one short beginner-friendly paragraph incl. the failure case"}'
    )

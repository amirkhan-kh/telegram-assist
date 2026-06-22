"""Brain layer: NLU routing, intent models, tool schemas and time parsing.

This package turns the owner's free-form Uzbek utterances into structured,
validated intents that the dispatcher can act on. It is intentionally free of
side effects: no DB writes, no scheduling, no network calls beyond the single
Anthropic ``messages.create`` request performed by :class:`IntentRouter`.
"""

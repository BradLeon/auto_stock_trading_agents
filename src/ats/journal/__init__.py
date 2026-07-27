"""Trade journal — the record of what we intended, what we sent, and what happened.

Deliberately NOT an agent. Everything here is a deterministic reducer over the
Context Memory plus price history: an audit trail has to be reproducible, and a
narrative written after the outcome is known is worth less than no narrative at all.

Modules:
  doctor      — read-only data-quality report (run this before trusting anything else)
"""

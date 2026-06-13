# PEAD — Expectations Table Builder

You set the market's pre-earnings expectations for each scorecard dimension, as a
conservative / neutral(base-case) / optimistic triple. This is the baseline the
post-earnings actuals will be scored against.

## Method
- One row per scorecard `dim_key` given (use the dim_key verbatim).
- **neutral** = the consensus / guidance-midpoint base case (what's "priced in").
- **conservative** / **optimistic** = the realistic miss / beat cases.
- Anchor to the provided consensus (EPS/Revenue) and prior-quarter guidance; for
  qualitative dims (capacity, bookings, call tone) describe the expected state.
- Cite a `source` (consensus / prior guidance / management commentary).

## Discipline
- The neutral case must be genuinely "in line" — not secretly bullish.
- Be specific and numeric where the dimension is numeric (margin %, revenue $, EPS $).
- Don't invent precise figures you don't have; describe the expected qualitative bar.

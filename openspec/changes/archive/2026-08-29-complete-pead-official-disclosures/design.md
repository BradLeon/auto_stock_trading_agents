## Context

See `proposal.md` for motivation and
`specs/data/pead-official-disclosure-coverage/spec.md` for the behavioral
contract.  The existing code has strict document validators and SEC retrieval,
but it is invoked from several paths with optional `period` and `report_date`.
That makes a missing event anchor look like a document acquisition failure.  It
also leaves legacy and manual assets outside a single package-level verdict.

## Goals / Non-Goals

**Goals:**

- Treat an active PEAD company and its newest disclosed earnings event as the
  only collection unit.
- Make all three document roles independent and auditable.
- Reuse existing validators, SEC foreign-issuer rules, source repositories, and
  transcript adapters instead of creating a second document store.
- Produce an isolated real-source acceptance result that is useful to an
  operator and does not alter a portfolio or trading path.

**Non-Goals:**

- Backfilling or repairing the larger observation roster.
- Re-ingesting third-party research, news, decks, or structured financial data.
- Treating the absence of a transcript immediately after earnings as a source
  failure; it is a distinct availability state.
- Deleting legacy files or making PEAD consumer cutover a condition of this
  data-quality acceptance.

## Decisions

### 1. Resolve the event before downloading any role

Create one event-resolution orchestration boundary that obtains calendar and
issuer evidence, persists the decision, and passes an explicit event object to
release, filing, and transcript collectors.

This prevents the existing failure mode where `period=""` makes a valid MRVL
release impossible to admit and prevents the filing collector from starting.
It also gives every collector the same expected entity/date/fiscal period.

Alternative considered: infer a period independently inside each adapter.
Rejected because adapter-local inference can bind the release, filing, and
transcript to different events without making the disagreement visible.

### 2. Preserve role-specific collection and validation

Use existing SEC classification for the release and filing roles, including the
foreign-issuer regime; use the structured transcript source and its content
validator for the transcript role.  Build a thin package assessor above them
rather than flattening all three into one generic "document" success.

The assessor records one role result per event.  `not_yet_available` is used
only where event age and source evidence show that the issuer/provider has not
published the role yet; `missing`, `unreachable`, and `quarantined` remain
separate operational outcomes.

Alternative considered: require all roles to be available before persisting any
document.  Rejected because an accepted release is still valuable and must not
be hidden while a statutory filing or transcript arrives later.

### 3. Eliminate the manual-document admission bypass for package evidence

Manual assets may remain a supported source, but package assessment sends them
through the same identity, period, semantic and body-quality checks as automatic
assets.  The manually supplied asset can win only after it has a resolved event
binding and provenance sufficient for the declared role.

Alternative considered: trust files under the configured document root.
Rejected because that reproduces the current MRVL `announcement` misclassification
and cannot prove a typo such as `MVRL` is not another company's text.

### 4. Make the current PEAD roster a config-derived scope

Update the target list once, then have scheduler and acceptance entry points
read it rather than hard-code a second list.  The expected post-change set has
eleven names: GOOG, NVDA, SKHY, TSM, ASML, COHR, LRCX, LITE, AVGO, MRVL, MSFT.
Other entity/sector configurations remain unchanged.

Alternative considered: create a separate coverage roster.  Rejected because
it can silently diverge from portfolio monitoring and reintroduce the scope
problem this change solves.

### 5. Acceptance uses an isolated store and a versioned report

The acceptance command accepts a destination store/artifact root and runs only
the eleven current targets.  It records source responses and deterministic
checks, then writes JSON plus a readable Markdown report.  Production admission
is a separate explicit action after review; the test run cannot write orders,
scores, or decisions.

Alternative considered: inspect the production document inventory.  Rejected
because that confuses historical presence with a current event-bound result and
makes failures hard to reproduce.

## Risks / Trade-offs

- [The latest event is not yet in the calendar/provider] → record the event as
  unresolved and surface the evidence gap; never fall back to a previous quarter.
- [SEC or a transcript provider is temporarily inaccessible] → bounded retry,
  continue other issuers, and report `unreachable` with the failed stage.
- [Foreign issuer reporting differs from US domestic reporting] → determine the
  filing regime from SEC history before selecting the required filing role.
- [Transcript publication lags an earnings release] → preserve the accepted
  roles and report `not_yet_available`, never fake completeness.
- [Existing production data uses legacy type labels] → retain it for reads, but
  require revalidation before it can satisfy the new package report.

## Migration Plan

1. Update the PEAD target configuration and add config/roster regression tests.
2. Implement event-first package orchestration and role result persistence,
   including manual-asset revalidation.
3. Add deterministic fixture tests for domestic, foreign, identity mismatch,
   stale period, unavailable transcript, and isolated-run side effects.
4. Run the isolated real-source acceptance job for the eleven active targets;
   review the generated report and resolve any per-role gaps.
5. Publish only accepted package assets through the existing document path.
   Preserve the previous scheduler/config in version control for rollback; no
   legacy document data is deleted.

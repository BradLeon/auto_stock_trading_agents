# pead-official-disclosure-coverage Specification

## Purpose

Define a bounded, event-based evidence package for every active PEAD company,
so official earnings documents are demonstrably current, correctly attributed,
and safe for PEAD research rather than merely present in a file inventory.

## Requirements

### Requirement: The active PEAD roster is the official-document coverage scope

The system SHALL derive the official-document collection and acceptance scope
from the current PEAD target roster. The roster SHALL include `MSFT` and SHALL
exclude `CRDO`, `VRT`, and `KLAC`. Removed symbols SHALL NOT be scheduled,
scored, or reported as PEAD coverage targets, while their historical documents
and availability to non-PEAD research SHALL remain intact.

#### Scenario: Target roster is updated

- **WHEN** an operator inspects or runs the PEAD official-document coverage job
- **THEN** it SHALL operate on `GOOG`, `NVDA`, `SKHY`, `TSM`, `ASML`, `COHR`, `LRCX`, `LITE`, `AVGO`, `MRVL`, and `MSFT`
- **AND** it SHALL NOT schedule `CRDO`, `VRT`, or `KLAC` as PEAD work

### Requirement: Each PEAD company has one resolved latest disclosed earnings event

Before any official document is admitted, the system SHALL resolve an event
containing the canonical entity, actual disclosure date, fiscal year, and
fiscal quarter from authoritative calendar, issuer/SEC metadata, or compatible
transcript evidence. The system SHALL retain the evidence and conflicts used
for that decision. An unknown, conflicting, or stale event SHALL prevent
automatic acceptance for that event rather than causing a file from a prior
period to be reused.

#### Scenario: Company fiscal years differ

- **WHEN** NVDA reports fiscal Q2 FY2027 and another covered issuer reports fiscal Q2 FY2026 in the same calendar period
- **THEN** the system SHALL preserve each issuer's own fiscal label and actual disclosure date
- **AND** it SHALL NOT use the calendar-quarter label alone to bind either document

#### Scenario: A release is found without an event period

- **WHEN** an SEC candidate is attributed to MRVL but its target event has no resolved fiscal period
- **THEN** the candidate SHALL remain quarantined with `period_unresolved`
- **AND** the coverage report SHALL show that event resolution, rather than SEC reachability, is the blocking condition

### Requirement: Each resolved event is assessed as an independent three-role disclosure package

For each resolved event, the system SHALL independently collect and validate a
company release, a regulatory filing, and an earnings-call transcript. A
domestic issuer's filing role SHALL use the applicable 10-Q or 10-K; a foreign
issuer SHALL use the applicable validated 6-K, 20-F, or 40-F role. The package
report SHALL expose each role as `accepted`, `missing`, `not_yet_available`,
`unreachable`, or `quarantined`, and SHALL NOT report an event package as
complete unless all required roles are accepted.

#### Scenario: A domestic PEAD issuer has released results but has not filed its 10-Q

- **WHEN** the release is accepted and the statutory filing is not yet available
- **THEN** the report SHALL mark the filing `not_yet_available` with the checked source and time
- **AND** it SHALL NOT call the event package complete or silently substitute the release for the filing

#### Scenario: A foreign issuer does not file a 10-Q

- **WHEN** a covered foreign issuer's current reporting regime requires a 6-K for interim reporting
- **THEN** the system SHALL seek and validate the appropriate 6-K regulatory filing
- **AND** it SHALL NOT label the role missing merely because no 10-Q exists

### Requirement: Acceptance proves identity, period, provenance, freshness, and body quality

An accepted package document SHALL prove the canonical issuer identity, target
event period, document role, official or declared source provenance, and
content completeness. The release and filing SHALL retain CIK, form, accession,
source URL, filing date, and report date when supplied by SEC. The transcript
SHALL retain its provider, report date, fiscal period, and validated
speaker/paragraph structure. A release associated with a different legal
issuer, an obsolete event, frontend boilerplate, or an unverified manual file
SHALL NOT satisfy a package role.

#### Scenario: Similar ticker or name is returned

- **WHEN** a candidate body or provenance resolves to a company other than the expected canonical issuer
- **THEN** the candidate SHALL be quarantined with an identity reason
- **AND** it SHALL not count toward the expected issuer's coverage

#### Scenario: Historical asset looks plausible

- **WHEN** a previously stored `release`, `transcript`, or manually supplied file is found for the target company
- **THEN** it SHALL count only after revalidation against the resolved event and required role contract
- **AND** a missing published date, period, or authoritative provenance SHALL be reported as a validation gap

### Requirement: Operators can reproduce a bounded acceptance report without trading side effects

The system SHALL provide a single operational entry point that collects or
revalidates official documents for the current PEAD roster in an isolated
store, and produces a per-entity report. The report SHALL state the roster
revision, resolved event, role-by-role status, source/form/accession, entity and
period checks, publication/fetch timestamps, text-quality checks, and failure
reasons. The acceptance run SHALL perform no LLM inference, PEAD scoring,
Chief processing, order submission, or broker-side trade action.

#### Scenario: Acceptance run has partial source failures

- **WHEN** one issuer's SEC endpoint is unreachable or its transcript is not yet published
- **THEN** the run SHALL continue for the remaining PEAD issuers
- **AND** the final report SHALL distinguish that local failure from a successful package for another issuer

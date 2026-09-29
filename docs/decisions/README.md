# Architecture Decision Records

This directory holds MAJEL's **Architecture Decision Records (ADRs)**.

An ADR is a short document that captures one significant architectural decision: the context that forced it, the decision itself, and its consequences. ADRs are immutable once accepted. A later decision **supersedes** an earlier one; it does not rewrite it.

## Why ADRs

MAJEL is an evolving architecture. Principles will be tested, and some will change. ADRs make sure that:

- the reasoning behind the architecture survives longer than the conversations that produced it
- contributors can see *why* something is the way it is before proposing to change it
- decisions trace back to evidence, ideally an [experiment](../experiments/README.md)
- reversals are explicit, with the original decision and its replacement both on record

## What deserves an ADR

Write an ADR when a decision is hard to reverse, affects more than one component, or changes a trust or credential boundary. Examples:

- how agents exchange results without exchanging credentials
- how agent identity is represented and persisted
- how capabilities are named and matched to providers
- what isolation is required for a given trust level

Routine implementation choices do not need an ADR.

## Format

Files are numbered sequentially and named `NNNN-short-title.md`, for example `0001-record-architecture-decisions.md`.

Each ADR should contain:

- **Title**
- **Status:** Proposed, Accepted, Superseded by ADR-NNNN, or Deprecated
- **Date**
- **Context:** the forces and constraints at play
- **Decision:** what was decided
- **Consequences:** what becomes easier, harder, or riskier as a result
- **Evidence:** links to the experiments or other material that informed the decision

## Index

*No ADRs have been recorded yet.*

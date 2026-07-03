# 00_PROJECT_BOOTSTRAP.md

## Purpose

This document is the entry point for every engineer or AI agent joining
the Software Factory Platform (SFP) project.

It provides the context required to understand the project before
reading any other artifact.

## What is SFP?

SFP is a deterministic software production system. Users own
architecture, engineering decisions and governance. AI agents execute
explicit specifications rather than inventing architecture.

## Core Philosophy

-   Architecture precedes implementation.
-   Engineering decisions precede coding.
-   AI agents execute specifications.
-   Software should be produced through software.
-   Explicit specifications are preferred over conversational knowledge.

## Artifact Hierarchy

The authoritative artifact chain is defined in Master Architecture
Specification §12.5. This project follows that chain:

1.  Master Architecture Specification
2.  Architecture Validation Specification
3.  Implementation Decisions
4.  Implementation Blueprint
5.  Engineering Backlog (Bootstrap Jira tickets)
6.  Implementation (Source Code)

Higher layers constrain lower layers. No derived artifact may contradict
a higher layer; where they conflict, the higher layer prevails.

### Aim

The terminal artifact is the Engineering Backlog ticket.

Every upstream layer exists to make each ticket fully deterministic, so
that a ticket can be executed by an AI agent without making any
unresolved architectural or implementation decision.

A ticket is executable only when the Master Architecture Specification,
the Architecture Validation Specification, the Implementation Decisions,
and the Implementation Blueprint have already resolved every question the
ticket raises (MAS §12.9).

### Related Artifacts

-   Implementation Notes are working-draft engineering conventions that
    feed into Implementation Decisions. They are not a separate
    architectural layer.
-   An AI Implementation Specification is the Engineering Backlog ticket
    in its AI-executable form. It is not a separate layer; it is the same
    ticket consumed by an AI agent.

## Current Status

-   Master Architecture Specification: Frozen v0.1.3
-   Architecture Validation Specification: Folded into the integration test suite (MAS §12.7 scenarios as required test cases); no standalone artifact
-   Implementation Decisions: Canonical v1.0 (IMPLEMENTATION_DECISIONS.md)
-   Implementation Blueprint: Not Started
-   Engineering Backlog: Not Started
-   Implementation Notes: Working Draft (feeds Implementation Decisions)

## Current Milestone

1.  ~~Review implementation decisions.~~ ✅
2.  ~~Produce IMPLEMENTATION_DECISIONS.md.~~ ✅
3.  Refine implementation notes (feed into Implementation Decisions).
4.  Design the Engineering Backlog ticket template (AI-executable form).
5.  Generate the bootstrap implementation backlog.

## Guidance

-   Treat documentation as authoritative.
-   Do not invent architecture.
-   Distinguish architecture, implementation decisions, implementation
    conventions and implementation tasks.
-   Prefer explicit documentation over conversational memory.

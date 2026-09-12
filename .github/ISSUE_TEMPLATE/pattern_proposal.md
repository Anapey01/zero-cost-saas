---
name: Propose a Free-Tier Pattern
about: Submit a new production-proven zero-cost architectural pattern
title: "[Pattern]: "
labels: pattern, enhancement
assignees: ''
---

## Pattern Overview
A concise 1-2 sentence description of the pattern.

## 1. The Constraint
What infrastructure limit or pricing model forced this architectural choice? (e.g. instance-hour limits, connection pooling, function timeouts, bandwidth limits).

## 2. The Option(s) Rejected & Why
What is the standard / conventional solution, and why does it break the \ run-rate constraint?

## 3. The Option Chosen
What did you build or configure instead? Provide code snippets, config files, or architectural flows.

## 4. The Honest Cost
What is the real tradeoff? (e.g., latency penalty, cold starts, client complexity, edge-case failure modes). Be transparent — no sales pitches.

## Production Verification
- [ ] This pattern has been tested in a real production or staging environment (not purely theoretical).
- [ ] No real secrets, tokens, private URLs, or proprietary domains are present in this proposal.

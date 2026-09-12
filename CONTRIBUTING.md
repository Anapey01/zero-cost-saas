# Contributing

This document is a living decision log. The goal is to collect patterns
that people have actually used in production at near-zero cost — not
hypothetical architectures.

## Adding a new pattern

1. **Fork and branch** from `main`.

2. **Add a section to README.md** following the existing structure:
   - The constraint that forced the decision
   - The option rejected and why
   - The option chosen
   - The honest cost paid in exchange

3. **Optionally add a skeleton to `/patterns/`:**
   - Create a new directory: `patterns/your-pattern-name/`
   - Keep the implementation under ~200 lines
   - Strip all business logic — the mechanism in isolation
   - Include a `README.md` in the directory

4. **Before submitting:**
   - No real domain names, API keys, credentials, or account details
   - No internal file paths or company-identifying structure
   - Test that any code you include actually runs

## What makes a good entry

- Real production use, not theory
- Specific numbers where possible (hours saved, cost avoided, latency added)
- Honest about the cost — every choice has one
- Written for competent developers, not beginners

## What doesn't belong here

- Patterns that require paid tiers to implement
- "Here's how to build X from scratch" tutorials
- Sales-pitch framing for any particular product

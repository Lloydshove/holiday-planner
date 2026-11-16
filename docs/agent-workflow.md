# Agent workflow

The intended workflow draws on the research-plan-implement-review pattern from
[Microsoft HVE Core](https://github.com/microsoft/hve-core), not a claim that
HVE Core is installed or that its profiles provide an autonomous scheduler.
The custom profiles in `.github/agents/` are repository-owned adaptations
written for this project. They can be selected in supported Copilot
environments, subject to your account and repository settings.

1. The owner writes a GitHub issue. `ready-for-agent` is the intended explicit
   opt-in, but currently does not trigger anything.
2. A requirements specialist clarifies material ambiguities and documents
   acceptance criteria. If a decision needs the owner, work stops.
3. An implementation specialist creates a scoped change and PR linked to the
   issue, including relevant tests and documentation.
4. A verification specialist compares the PR against requirements and checks
   for regressions, accessibility and privacy risks. Failures are reported.
5. After required checks pass, the desired end state is automatic merging.
   It is **not configured**: it requires an available orchestration service,
   an authorized merge mechanism, actual mandatory CI and branch protection,
   duplicate-run handling and an agreed policy for exceptions. A passing test
   alone cannot establish that the issue is adequately specified or safe.

Profiles do not automatically coordinate, run on labels, or enforce
permissions. Cloud automation tools were not available during bootstrap; no
automations or group have been created or activated. Until those dependencies
are addressed, assign work manually and review changes.

When automation becomes available, use personal ownership as requested,
event-driven triggering on the opt-in label, narrow permissions, and bounded
handoffs. Detect existing issue/PR work before starting and before creating a
PR; these checks reduce duplicates but are not an atomic lock. Block and ask
the owner when requirements or validation are uncertain. Keep automatic merge
disabled until mandatory checks and the authorized merge path are verified.

An optional future feedback loop could examine the proportion of eligible
issues that are completed without later regressions, along with reopened
issues and maintainer corrections, using issues and PRs as evidence. Missing
or immature results are unknown, not successes. A separate proposal-only
review could suggest changes for the owner to approve; this adds run cost and
review effort and has not been enabled.

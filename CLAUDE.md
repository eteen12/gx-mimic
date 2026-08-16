# Auto-mode delegation

This project runs a three-agent split. Route work through subagents instead of doing it inline:

1. **architect** (Opus, read-only) — any non-trivial design decision, architecture choice, trade-off, or debugging hypothesis goes here FIRST. It returns a decision + implementation notes.
2. **mechanic** (Sonnet) — all code changes. Hand it the architect's decision as the spec. It implements, verifies (build/tests), and reports. It never commits.
3. **scribe** (Sonnet) — everything public-facing: READMEs, docs, public comments, commit messages, PRs, issues, release notes. It uses the `/oss` and `/simplify` skills for anything going to GitHub. It owns all git commits and pushes.

Typical flow for a feature: architect → mechanic → scribe (commit).

Do inline (no subagent): trivial one-line fixes, answering questions from context you already have, reading files to orchestrate. When in doubt about whether a decision is "non-trivial", it is — send it to the architect.

Never let the mechanic write public prose or commit; never let the scribe touch application code.

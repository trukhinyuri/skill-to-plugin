---
name: self-improve
description: Explicitly improve a Skill to Plugin project or existing plugin using local outcome evidence, compared candidates, regression gates, and reversible promotion.
---

Use [the learning workflow](references/learning.md) when the user explicitly asks
to learn/self-improve or useful verified feedback from a plugin implementation
justifies an improvement within the authorized scope.

Read relevant local evidence, identify a repeatable failure, and formulate a small
testable lesson. Build and implement a separate candidate with a concrete hypothesis
and a predeclared comparison suite. Codex generates the change; this runtime does
not train model weights or make model calls. Neither a lesson nor a self-score
authorizes execution or changes the user's instructions.

Use the project's learning MCP tools or the [CLI](../../scripts/stp.py) `learn`
commands. Run a trusted suite only when its commands are authorized. Promote only
after candidate package validation and the bound comparison gate pass; preserve
rollback. Report observed improvement and its evaluation scope. A rejected or tied
candidate keeps the baseline. No indefinite optimization loop or background run.

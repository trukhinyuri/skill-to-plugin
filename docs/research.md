# Design evidence — 2026-10-01

## Native packaging

OpenAI documents portable root `plugin.json`, `skills/` and optional `mcp.json`,
plus a `.codex-plugin/plugin.json` compatibility overlay. Inline OpenAI extensions
replace that overlay's OpenAI settings. Marketplace catalogs permit repo sources
and native install commands. These facts justify a relocatable dual-format package;
GitHub distribution remains distinct from universal directory submission.
[OpenAI packaging documentation](https://developers.openai.com/plugins/build/plugins).

The local CLI is 0.159.2. Its help exposes `plugin marketplace add`, `plugin add`,
and `plugin list --json`; these are used instead of manually replacing host config.
Installed native examples confirm `skills` and MCP path wiring. Package validation
is explicitly narrower than host behavior verification.

## Learning mechanism

[Reflexion](https://arxiv.org/abs/2303.11366) studies linguistic feedback and episodic
memory without updating model weights. It supports separating proposed lessons
from executable implementation, but its benchmark results do not establish this
plugin's quality.

[Self-Refine](https://arxiv.org/abs/2303.17651) studies feedback-driven iterative
revision. This design uses a bounded host-generated candidate and objective tests
because self-feedback alone cannot certify success.

[GEPA](https://arxiv.org/abs/2507.19457) studies reflective evolution of prompts.
Its results motivate measured candidate comparison; this implementation is not
GEPA and does not claim its benchmark performance. A single justified candidate
keeps the local workflow economical and interpretable.

Our engineering inference is to keep observed feedback as local data, have Codex
author narrow changes, execute predeclared tests on copied inputs, bind results to
content hashes, block regressions, and preserve rollback. Neither a self-score nor
agreement among agents substitutes for executed checks. Held-out host scenarios
are necessary for claims about semantic behavior or generalization.

## MCP and limits

The stdio server follows newline-delimited JSON-RPC and reserves stdout for MCP
messages, as specified by the [MCP transport standard](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).
It supplies tool discovery and calls without a network listener or external SDK.

The package cannot implement unavailable connectors or know arbitrary dynamic
dependencies automatically. The compiler reports packaging evidence; the host
implements semantic behavior and records unresolved requirements. World-best or
frontier superiority would require a representative comparative benchmark with
declared baselines, budgets and repeated trials. No such claim is made here.

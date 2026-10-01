# From skill to working plugin

Start with the requested observable outcome and target clients. Read every input
SKILL.md and the resources needed to understand its behavior. Inventory scripts,
templates, references, invocation policy, tools, secrets, local paths, licenses,
and dependencies. Translate the in-scope behavior into a project-local JSON
contract using [the contract template](../../../templates/contract.json): requirement,
source location, implementation location, scenario, evidence, and status. The
host authors this semantic contract; the compiler cannot infer completeness.

Choose the lightest native architecture that implements the behavior. Skills can
coordinate existing host tools; reusable deterministic work belongs in bundled
scripts; new tools justify an MCP server. Do not invent integrations, copy the
author's private configuration, or add hooks/background jobs merely to appear
more autonomous. A missing connector is an explicit dependency until available.
Research the current authoritative host/tool documentation for uncertain APIs.

`inspect_skills` accepts skill directories or SKILL.md files. Read its dependency
report. `compile_plugin` takes `sources`, `output`, `name`, and optional `version`,
`description`, `existing`, `resource_roots`. Its output must be a separate absent
directory. External resources require explicit roots; Markdown references are
relocated into the package and missing resources fail compilation. Scripts,
assets, optional metadata and invocation policies are retained. It executes none
of them. Review reported absolute paths in code, prose and configuration; this
scanner cannot discover every dynamic reference. Adapt those paths and external
runtime assumptions before claiming portability.

For an update, pass the current plugin as `existing`, retain its name, and choose
the intended new version. The compiler preserves unrelated files and replaces
only specified skill directories. Implement additional code in that candidate;
do not silently overwrite the live plugin. Preserve the baseline and evaluate
regressions before a reversible promotion.

For every requirement, implement the behavior with available tools, then obtain
evidence that distinguishes a working implementation from a plausible artifact.
Examples: call the generated tool, run its actual script with Unicode and empty
input, resolve a resource after relocation, and exercise update compatibility.
Use an independent reviewer when the ambiguity or consequences justify it. Add
an acceptance case for each material failure. Do not weaken expected results to
turn a failed candidate green. A held-out scenario helps test generalization;
repeat nondeterministic/model evaluations enough to distinguish noise, within
the user's budget, and avoid global superiority claims from a small local suite.

Package layout supports root `plugin.json`, `mcp.json`, `skills/`, resources, and
the `.codex-plugin/plugin.json` compatibility overlay. Portable OpenAI extensions
take precedence over the overlay. Keep identity/version synchronized. Validate
paths and behavior, then follow the user's authorization for publishing/installing.
GitHub publication is distinct from admission to the universal plugin directory.

Use the installed client's supported `codex plugin marketplace add` and
`codex plugin add` commands, or its plugin UI. Inspect their current help first.
Confirm enabled state, installed skill discovery, installed MCP startup and a
meaningful tool call. A restart or new chat may be needed for discovery; don't
restart the user's active app or interrupt other work without reason. If client
reload is unavailable, report precisely which verification remains.

Done requires all in-scope contract requirements verified, no material regression,
valid package, documented invocation, and authoritative publication/install checks
when requested. Leave unverified behavior visible; do not relabel packaging as
complete implementation.

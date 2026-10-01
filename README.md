# Skill to Plugin

Create a complete Codex plugin from one skill or a set of skills, implement missing
behavior, improve an existing plugin, and learn from verified outcomes.

Codex provides reasoning and code generation through two bundled skills. The
dependency-free Python runtime provides deterministic packaging and eight MCP
tools for inventory, compilation, validation, feedback, evaluation, promotion and
rollback. No separate API key, model subscription, daemon, or weight training.

## Install in Codex

Python 3.11+ and a Codex client supporting plugin marketplaces are required.

```sh
codex plugin marketplace add trukhinyuri/skill-to-plugin
codex plugin add skill-to-plugin@skill-to-plugin
```

The repository includes portable `plugin.json` / `mcp.json` and the native
`.codex-plugin/plugin.json` / `.mcp.json` compatibility layout. Desktop and CLI
share the installed plugin cache on this host. Refresh Plugins or open a new chat
if the running client has not reloaded its skill catalog. Installation does not
publish the plugin to OpenAI's universal directory.

## Use it

Select **Skill to Plugin** in the plugin picker, or ask Codex:

> Use Skill to Plugin to fully implement a plugin from `/path/to/skill` and
> `/path/to/second-skill`. Save it in `/path/to/output`, verify every behavior,
> and report any dependency you cannot implement.

> Use Skill to Plugin to improve `/path/to/existing-plugin`, preserving its
> interfaces and checking regressions.

> Use self-improve for this project. Read local feedback, implement a justified
> candidate, compare it with the baseline, and promote only verified improvement.

The skills are `skill-to-plugin` and `self-improve`, namespaced by their plugin
when the host requires that. Ordinary workflows can record proposed lessons after
useful observed results; explicit self-improvement runs the full comparison cycle.
Learned text remains project-local evidence until a verified candidate is promoted.

## Direct CLI

The checkout and installed cache both contain a relocatable entrypoint:

```sh
python3 scripts/stp.py inspect examples/normalize-text
python3 scripts/stp.py build examples/normalize-text --name text-tools --output /tmp/text-tools
python3 /tmp/text-tools/skills/normalize-text/scripts/normalize.py '  Привет   世界  '
python3 scripts/stp.py validate /tmp/text-tools
```

For updates, add `--existing BASELINE --plugin-version 0.2.0` and use a separate
output directory. For external Markdown resources, explicitly grant their trees
with `--resource-root PATH`. Inputs are read as data; compilation never runs their
instructions or scripts. Code references and dynamically loaded dependencies also
need host inspection and implementation. See the [implementation workflow](skills/skill-to-plugin/references/implementation.md).

## Measured self-improvement

State lives in the working project's ignored `.skill-to-plugin/`, never global
instructions or the installed plugin cache. `learn record` stores observed evidence
and proposed lessons; Codex turns relevant lessons into candidate code/instructions.
`learn evaluate` runs an explicitly authorized external trusted suite against
separate copies of baseline and candidate. Commands run without a shell, with
time/output limits. This is not an OS sandbox.

Promotion requires actual recorded results bound to unchanged input hashes,
strict improvement, all candidate cases passing, no regressions, and package
validation. Promotion saves a verified backup; rollback refuses to overwrite later
edits. Ties and failures retain the baseline. See [learning usage](skills/self-improve/references/learning.md).

This measures the chosen behaviors. It does not prove global superiority, guaranteed
reasoning improvement, or success for every skill. Semantic completion requires a
host-authored requirement contract and realistic behavior tests. Host executables
and environment are not fingerprinted. After abrupt termination during replacement,
retain the backup and review the stale lock/sibling directories before recovery.

## Development and evidence

```sh
python3 -m unittest discover -s tests -v
python3 scripts/stp.py validate .
```

The suite covers conversion, resource closure/relocation, existing-plugin updates,
MCP lifecycle and calls, measured improvements/regressions, stale/tampered evidence,
rollback, bounded execution, and local state constraints. Behavioral testing of
the skills uses an independent synthetic conversion scenario. Research basis and
the limits of the quality claim are in [research notes](docs/research.md).

MIT licensed. No telemetry, background collection, remote inference or automatic
publishing. User task authorization and host permissions remain authoritative.

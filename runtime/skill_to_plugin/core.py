"""Deterministic compiler. Source instructions are data, never executed here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit

IGNORE = {".git", "__pycache__", ".DS_Store", ".skill-to-plugin", ".pytest_cache"}
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
LINK = re.compile(r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^\s)]+)([^)\n]*\))")
REFERENCE = re.compile(r"^( {0,3}\[[^\]\n]+\]:[ \t]*)(<[^>\n]+>|\S+)([^\n]*)$", re.MULTILINE)
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024


class PluginError(ValueError):
    pass


def check_name(name: str) -> str:
    if not isinstance(name, str) or len(name) > 64 or not NAME.fullmatch(name):
        raise PluginError("Name must be kebab-case, at most 64 characters")
    return name


def safe_path(path: str | Path, *, exists: bool = True) -> Path:
    """Reject symbolic links before resolving, including parent components."""
    p = Path(path).expanduser().absolute()
    for alias, target in ((Path("/var"), Path("/private/var")), (Path("/tmp"), Path("/private/tmp"))):
        if p.is_relative_to(alias) and alias.is_symlink() and alias.resolve() == target:
            p = target / p.relative_to(alias)
    for part in (p, *p.parents):
        if part.is_symlink():
            raise PluginError(f"Symlink path is unsupported: {part}")
    if exists and not p.exists():
        raise PluginError(f"Path does not exist: {p}")
    return p.resolve()


def files(root: Path) -> list[Path]:
    result, total = [], 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in dirs + names:
            if (base / name).is_symlink():
                raise PluginError(f"Symlinks are unsupported: {base / name}")
        dirs[:] = sorted(d for d in dirs if d not in IGNORE)
        for name in sorted(names):
            if name in IGNORE:
                continue
            p = base / name
            if not p.is_file():
                raise PluginError(f"Not a regular file: {p}")
            if name == ".env" or name.startswith(".env.") or name in {"auth.json", "credentials.json", "id_rsa", "id_ed25519"}:
                raise PluginError(f"Credential-like file must be removed from package input: {p}")
            size = p.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL:
                raise PluginError("Package input exceeds 16 MiB/file or 64 MiB/tree")
            result.append(p)
    return result


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in files(root):
        data = p.read_bytes()
        h.update(str(p.relative_to(root)).encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def json_write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def frontmatter(path: Path) -> dict:
    """Read required top-level scalar fields; retain original YAML bytes when copying.

    This is intentionally not a general YAML parser. Complex required fields are
    rejected, rather than guessed; nested optional metadata remains untouched.
    """
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PluginError(f"Missing YAML frontmatter: {path}")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as e:
        raise PluginError(f"Unclosed frontmatter: {path}") from e
    fields = {}
    for i, line in enumerate(lines[1:end], 1):
        m = re.match(r"^(name|description):\s*(.*?)\s*$", line)
        if not m:
            continue
        key, value = m.groups()
        if key in fields:
            raise PluginError(f"Duplicate {key} in {path}")
        if value in {">", ">-", ">+", "|", "|-", "|+"}:
            block = []
            for following in lines[i + 1:end]:
                if following and not following[0].isspace():
                    break
                block.append(following.strip())
            value = ("\n" if value.startswith("|") else " ").join(block).strip()
        elif value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as e:
                raise PluginError(f"Invalid quoted {key} in {path}") from e
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if not isinstance(value, str) or not value or value.startswith(("{", "[", "&", "*", "!")):
            raise PluginError(f"{key} must be a nonempty YAML scalar in {path}")
        fields[key] = value
    if "name" not in fields or "description" not in fields:
        raise PluginError(f"Skill needs name and description: {path}")
    check_name(fields["name"])
    return fields


def link_target(source: Path, raw: str) -> tuple[Path | None, str]:
    raw = raw.strip("<>")
    if raw.startswith("#") or not raw:
        return None, ""
    u = urlsplit(raw)
    if u.scheme or u.netloc or "${" in raw:
        return None, ""
    p = Path(unquote(u.path)).expanduser()
    raw_path = p if p.is_absolute() else source.parent / p
    suffix = ("?" + u.query if u.query else "") + ("#" + u.fragment if u.fragment else "")
    return safe_path(raw_path, exists=False), suffix


def markdown_targets(text: str):
    for pattern in (LINK, REFERENCE):
        yield from pattern.finditer(text)


def inspect(sources: list[str | Path]) -> dict:
    if not sources:
        raise PluginError("At least one skill is required")
    result, seen = [], set()
    for source in sources:
        root = safe_path(source)
        if root.is_file():
            if root.name != "SKILL.md":
                raise PluginError("Input file must be named SKILL.md")
            root = root.parent
        entry = root / "SKILL.md"
        if not entry.is_file():
            raise PluginError(f"No SKILL.md at {root}")
        meta = frontmatter(entry)
        if meta["name"] in seen:
            raise PluginError(f"Duplicate skill name: {meta['name']}")
        seen.add(meta["name"])
        inventory = files(root)
        links, absolute = [], set()
        for p in inventory:
            if p.suffix.lower() not in {".md", ".py", ".sh", ".js", ".mjs", ".ts", ".tsx", ".yaml", ".yml", ".json", ".toml", ".txt"}:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            absolute.update(re.findall(r"(?:/Users/|/home/|~/)[^\s`<>\])]+", text))
            if p.suffix.lower() != ".md":
                continue
            for m in markdown_targets(text):
                target, _ = link_target(p, m[2])
                if target:
                    links.append({"from": str(p.relative_to(root)), "target": str(target), "exists": target.exists(),
                                  "insideSkill": target.is_relative_to(root)})
        result.append({"name": meta["name"], "description": meta["description"], "root": str(root),
                       "digest": digest(root), "fileCount": len(inventory),
                       "resources": [str(p.relative_to(root)) for p in inventory],
                       "localLinks": links, "absolutePathsToReview": sorted(absolute),
                       "executableResources": [str(p.relative_to(root)) for p in inventory
                                               if p.suffix in {".py", ".sh", ".js", ".mjs", ".ts"}]})
    return {"skills": result, "semanticImplementation": "requires host-authored contract and behavior checks",
            "sourceExecution": "none"}


def _copy_existing(existing: Path, stage: Path) -> None:
    for p in files(existing):
        dest = stage / p.relative_to(existing)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)


def compile_plugin(sources: list[str | Path], output: str | Path, name: str, *,
                   description: str | None = None, version: str = "0.1.0",
                   existing: str | Path | None = None, resource_roots: list[str | Path] | None = None) -> dict:
    check_name(name)
    if not VERSION.fullmatch(version):
        raise PluginError("Version must be semantic version, e.g. 1.2.3")
    report = inspect(sources)
    roots = [Path(s["root"]) for s in report["skills"]]
    approved = [safe_path(r) for r in resource_roots or []]
    out = safe_path(output, exists=False)
    if out.exists():
        raise PluginError("Output already exists; create a separate candidate")
    if any(out.is_relative_to(r) or r.is_relative_to(out) for r in roots + approved):
        raise PluginError("Output and inputs must be separate trees")
    base = safe_path(existing) if existing else None
    base_digest = digest(base) if base else None
    if base and (out.is_relative_to(base) or base.is_relative_to(out)):
        raise PluginError("Existing plugin and candidate must be separate trees")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=out.parent))
    try:
        manifest = {}
        if base:
            validation = validate_plugin(base)
            if not validation["valid"]:
                raise PluginError("Existing plugin is invalid: " + "; ".join(validation["errors"]))
            _copy_existing(base, stage)
            manifest = json.loads((stage / ".codex-plugin/plugin.json").read_text()) if (stage / ".codex-plugin/plugin.json").exists() else {}
            identity = json.loads((stage / "plugin.json").read_text()) if (stage / "plugin.json").exists() else manifest
            if identity.get("name") != name:
                raise PluginError("Existing plugin identity must match requested name")
            active_overlay = identity.get("extensions", {}).get("com.openai", manifest)
            active_skills = (base / active_overlay.get("skills", "./skills/")).resolve()
            if active_skills != base / "skills" and active_skills.is_dir():
                for p in files(active_skills):
                    dest = stage / "skills" / p.relative_to(active_skills)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
        mapping: dict[Path, Path] = {}
        owner_dest = {r: stage / "resources" / f"vendor-{i + 1}" for i, r in enumerate(approved)}
        for skill, root in zip(report["skills"], roots):
            destroot = stage / "skills" / skill["name"]
            owner_dest[root] = destroot
            if destroot.exists():
                shutil.rmtree(destroot)
            for p in files(root):
                mapping[p] = destroot / p.relative_to(root)
        # Follow Markdown resource links only within explicitly granted trees.
        queue = list(mapping)
        for p in queue:
            if p.suffix.lower() != ".md":
                continue
            for m in markdown_targets(p.read_text(encoding="utf-8")):
                target, _ = link_target(p, m[2])
                if not target:
                    continue
                if not target.exists():
                    raise PluginError(f"Missing resource {m[2]} referenced by {p}")
                safe_path(target)
                if target in mapping:
                    continue
                owner = next((r for r in roots + approved if target.is_relative_to(r)), None)
                if owner is None:
                    raise PluginError(f"External resource requires --resource-root: {target}")
                added = files(target) if target.is_dir() else [target]
                for resource in added:
                    safe_path(resource)
                    if resource not in mapping:
                        mapping[resource] = owner_dest[owner] / resource.relative_to(owner)
                        queue.append(resource)
        total = 0
        rewritten = []
        for source, dest in mapping.items():
            total += source.stat().st_size
            if source.stat().st_size > MAX_FILE or total > MAX_TOTAL:
                raise PluginError("Compiled resources exceed size limits")
            if source.name == ".env" or source.name.startswith(".env.") or source.name in {"auth.json", "credentials.json", "id_rsa", "id_ed25519"}:
                raise PluginError("Credential-like resource cannot be packaged")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() == ".md":
                original = source.read_text(encoding="utf-8")
                def rewrite(match):
                    target, fragment = link_target(source, match[2])
                    if target is None:
                        return match[0]
                    mapped = mapping.get(target)
                    if mapped is None and target.is_dir():
                        owner = next((r for r in owner_dest if target.is_relative_to(r)), None)
                        mapped = owner_dest[owner] / target.relative_to(owner) if owner else None
                    if mapped is None:
                        raise PluginError(f"Resource cannot be relocated: {target}")
                    relative = os.path.relpath(mapped, dest.parent).replace(os.sep, "/") + fragment
                    value = f"<{relative}>" if " " in relative else relative
                    return match[1] + value + match[3]
                transformed = REFERENCE.sub(rewrite, LINK.sub(rewrite, original))
                if transformed == original:
                    shutil.copy2(source, dest)
                else:
                    dest.write_text(transformed, encoding="utf-8")
                    shutil.copymode(source, dest)
                    rewritten.append(str(dest.relative_to(stage)))
            else:
                shutil.copy2(source, dest)
        summary = description or " / ".join(s["description"] for s in report["skills"])
        portable = json.loads((stage / "plugin.json").read_text()) if (stage / "plugin.json").exists() else {}
        portable.update({"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                         "name": name, "version": version, "description": summary})
        if isinstance(portable.get("extensions", {}).get("com.openai"), dict):
            portable["extensions"]["com.openai"]["skills"] = "./skills/"
        manifest.update({"name": name, "version": version, "description": summary, "skills": "./skills/"})
        manifest.setdefault("interface", {"displayName": name.replace("-", " ").title(), "shortDescription": summary[:100],
                                          "category": "Developer Tools", "capabilities": ["Read", "Write"]})
        json_write(stage / "plugin.json", portable)
        json_write(stage / ".codex-plugin/plugin.json", manifest)
        json_write(stage / ".agents/plugins/marketplace.json", marketplace(name))
        # Provenance deliberately omits original machine paths and task feedback.
        json_write(stage / "provenance.json", {"formatVersion": 1,
                   "sources": [{k: s[k] for k in ("name", "digest", "fileCount")} for s in report["skills"]],
                   "rewrittenMarkdown": rewritten, "previousDigest": base_digest})
        validation = validate_plugin(stage)
        if not validation["valid"]:
            raise PluginError("Candidate failed validation: " + "; ".join(validation["errors"]))
        for skill, root in zip(report["skills"], roots):
            if digest(root) != skill["digest"]:
                raise PluginError("Source changed during compilation")
        if base and digest(base) != json.loads((stage / "provenance.json").read_text())["previousDigest"]:
            raise PluginError("Existing plugin changed during compilation")
        if out.exists():
            raise PluginError("Output appeared during compilation")
        stage.rename(out)
        return {"output": str(out), "name": name, "version": version, "skills": [s["name"] for s in report["skills"]],
                "digest": digest(out), "validation": validation,
                "semanticImplementation": "unverified until host-authored behavior contract is checked",
                "absolutePathsToReview": sorted({p for s in report["skills"] for p in s["absolutePathsToReview"]})}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def marketplace(name: str) -> dict:
    return {"name": name, "interface": {"displayName": name.replace("-", " ").title()},
            "plugins": [{"name": name, "source": {"source": "local", "path": "./"},
                         "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                         "category": "Developer Tools"}]}


def validate_plugin(path: str | Path) -> dict:
    root = safe_path(path)
    errors, warnings, skills = [], [], []
    try:
        inventory = files(root)
        candidates = [p for p in (root / "plugin.json", root / ".codex-plugin/plugin.json") if p.exists()]
        if not candidates:
            raise PluginError("No portable or Codex plugin manifest")
        manifests = [json.loads(p.read_text(encoding="utf-8")) for p in candidates]
        for manifest in manifests:
            check_name(manifest.get("name"))
            if not isinstance(manifest.get("description"), str) or not manifest["description"].strip():
                raise PluginError("Manifest needs nonempty description")
            if not VERSION.fullmatch(str(manifest.get("version", ""))):
                raise PluginError("Manifest needs semantic version")
        for field in ("name", "version"):
            if len({m.get(field) for m in manifests}) > 1:
                errors.append(f"Manifest {field} identities differ")
        portable = next((m for p, m in zip(candidates, manifests) if p.name == "plugin.json" and p.parent == root), None)
        overlay = (portable or {}).get("extensions", {}).get("com.openai")
        if overlay is None:
            overlay = next((m for p, m in zip(candidates, manifests) if p.parent.name == ".codex-plugin"), {})
        for key in ("skills", "mcpServers", "apps"):
            value = overlay.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                target = (root / value).resolve()
                if not value.startswith("./") or not target.is_relative_to(root) or not target.exists():
                    errors.append(f"Invalid manifest path for {key}")
        interface = overlay.get("interface", {})
        for value in [interface.get("composerIcon"), interface.get("logo"), *interface.get("screenshots", [])]:
            if value is None:
                continue
            if not isinstance(value, str) or not value.startswith("./"):
                errors.append("Interface assets must be ./ package paths")
            else:
                target = (root / value).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    errors.append(f"Missing interface asset: {value}")
        skilldir = (root / overlay.get("skills", "./skills/")).resolve()
        if not skilldir.is_relative_to(root):
            errors.append("Skills path escapes plugin")
        elif skilldir.exists():
            for entry in sorted(skilldir.glob("*/SKILL.md")):
                meta = frontmatter(entry)
                if entry.parent.name != meta["name"]:
                    errors.append(f"Skill folder/name differ: {entry.parent.name}")
                skills.append(meta["name"])
        for p in inventory:
            if p.suffix.lower() == ".md":
                for m in markdown_targets(p.read_text(encoding="utf-8")):
                    target, _ = link_target(p, m[2])
                    if target and (not target.is_relative_to(root) or not target.exists()):
                        errors.append(f"Unresolved local link in {p.relative_to(root)}: {m[2]}")
        for p in (root / "mcp.json", root / ".mcp.json"):
            if not p.exists():
                continue
            config = json.loads(p.read_text(encoding="utf-8"))
            servers = config.get("mcpServers")
            if not isinstance(servers, dict):
                errors.append(f"Invalid MCP server map: {p.name}")
            else:
                for name, server in servers.items():
                    if not isinstance(server, dict) or not (server.get("command") or server.get("url")):
                        errors.append(f"MCP server needs command or URL: {name}")
        if not skills:
            warnings.append("No skills discovered; this may be an MCP-only plugin")
    except (PluginError, ValueError, OSError, TypeError, AttributeError) as e:
        errors.append(str(e))
    return {"valid": not errors, "errors": errors, "warnings": warnings, "skills": skills,
            "coverage": "package paths, identity, frontmatter, linked resources, basic MCP shape; behavior requires separate evaluation"}

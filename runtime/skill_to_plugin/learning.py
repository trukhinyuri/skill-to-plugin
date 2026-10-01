"""Project-local evidence, measured candidate evaluation, and guarded promotion.

This module does not call models, train weights, or install lessons as instructions.
``allow_exec`` authorizes arbitrary subprocess code; it is not an OS sandbox.
Commands run in separate copies of baseline and candidate content. A command that
changes either copy invalidates its evaluation. Local authentication detects edited
or invented records; it is not protection against an owner who can read the key.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any

MAX_TREE_BYTES = 64 * 1024 * 1024
MAX_FILES = 10_000
MAX_JSON_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_OUTPUT_PREVIEW = 4 * 1024
MAX_CASES = 100
MAX_TIMEOUT = 60.0
MAX_EVENTS = 1000
MAX_EVALUATIONS = 100
MAX_PROMOTIONS = 100
SKIP = {".git", ".skill-to-plugin", "__pycache__"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _safe_path(path: Path, *, exists: bool = True) -> Path:
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise ValueError("Parent traversal is not allowed")
    path = Path(os.path.abspath(path))
    # macOS exposes its temporary directories through these system aliases.
    # Normalize only their exact OS targets, never user-controlled symlinks.
    for alias, target in ((Path("/var"), Path("/private/var")),
                          (Path("/tmp"), Path("/private/tmp"))):
        if path.is_relative_to(alias) and alias.is_symlink() and alias.resolve() == target:
            path = target / path.relative_to(alias)
    for part in [*reversed(path.parents), path]:
        try:
            if stat.S_ISLNK(part.lstat().st_mode):
                raise ValueError(f"Symlink is not allowed: {part}")
        except FileNotFoundError:
            if exists or part != path:
                raise
    if exists and not path.exists():
        raise FileNotFoundError(path)
    return path


def _store(path: Path, *, create: bool = True) -> Path:
    path = _safe_path(path, exists=False)
    if path.name != ".skill-to-plugin":
        raise ValueError("Evidence store must be named .skill-to-plugin")
    forbidden = {".codex", ".agents", ".cache", "cache", "caches"}
    if any(part.lower() in forbidden for part in path.parent.parts):
        raise ValueError("Evidence store cannot be an installed cache or global instructions")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError("Evidence store must be a directory")
    for name in ("events", "evaluations", "promotions", "rollbacks"):
        child = _safe_path(path / name, exists=False)
        if create:
            child.mkdir(mode=0o700, exist_ok=True)
        if not child.is_dir():
            raise ValueError("Unsafe evidence store")
    return path


@contextlib.contextmanager
def _lock(store: Path):
    lock = store / ".lock"
    deadline = time.monotonic() + 5
    while True:
        _safe_path(lock, exists=False)
        try:
            lock.mkdir(mode=0o700)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError("Evidence store is busy; stale locks require owner review")
            time.sleep(0.02)
    try:
        yield
    finally:
        lock.rmdir()


def _identity(path: Path) -> dict[str, int]:
    value = path.stat()
    return {"device": value.st_dev, "inode": value.st_ino}


def _read_bytes(path: Path, limit: int = MAX_JSON_BYTES) -> bytes:
    path = _safe_path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Expected a regular file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds the size limit")
    return data


def _json_read(path: Path) -> dict:
    value = json.loads(_read_bytes(path, MAX_RECORD_BYTES))
    if not isinstance(value, dict):
        raise ValueError("Record must be a JSON object")
    return value


def _write_json(path: Path, value: dict) -> None:
    data = _canonical(value)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("Record exceeds the size limit")
    _safe_path(path, exists=False)
    temporary = path.parent / f".{path.name}.{uuid.uuid4()}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # link is atomic and refuses to overwrite an existing immutable record.
        os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _key(store: Path) -> bytes:
    path = _safe_path(store / ".record-key", exists=False)
    if not path.exists():
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(os.urandom(32))
            stream.flush()
            os.fsync(stream.fileno())
    key = _read_bytes(path, 32)
    if len(key) != 32:
        raise ValueError("Invalid local record key")
    return key


def _sign(store: Path, record: dict) -> dict:
    record = dict(record)
    record["signature"] = hmac.new(_key(store), _canonical(record), hashlib.sha256).hexdigest()
    return record


def _verified(store: Path, path: Path) -> dict:
    record = _json_read(path)
    signature = record.pop("signature", None)
    key = _read_bytes(store / ".record-key", 32)
    if len(key) != 32:
        raise ValueError("Invalid local record key")
    expected = hmac.new(key, _canonical(record), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("Record integrity check failed")
    return record


def _id(value: str) -> str:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid record ID") from None
    return value


def _capacity(directory: Path, maximum: int) -> None:
    # Refuse growth rather than silently deleting durable evidence/backups.
    count = 0
    for _ in directory.iterdir():
        count += 1
        if count >= maximum:
            raise ValueError("Local evidence limit reached; review and archive old records explicitly")


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > limit:
        raise ValueError(f"{name} must be nonempty text of at most {limit} bytes")
    return value.strip()


def _scan(root: Path) -> tuple[str, list[tuple[Path, int]]]:
    root = _safe_path(root)
    if not root.is_dir():
        raise ValueError("Plugin content must be a directory")
    digest = hashlib.sha256()
    digest.update(_canonical([".", "dir", stat.S_IMODE(root.stat().st_mode)]))
    entries: list[tuple[Path, int]] = []
    total = 0
    def visit(directory: Path) -> None:
        nonlocal total
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"Unsafe tree entry: {path}")
            if path.name in SKIP:
                continue
            relative = path.relative_to(root)
            entries.append((relative, stat.S_IMODE(mode)))
            if len(entries) > MAX_FILES or len(relative.parts) > 100:
                raise ValueError("Plugin tree exceeds the entry/depth limit")
            header = _canonical([relative.as_posix(), "dir" if path.is_dir() else "file", stat.S_IMODE(mode)])
            digest.update(len(header).to_bytes(8, "big")); digest.update(header)
            if path.is_dir():
                visit(path)
            else:
                data = _read_bytes(path, MAX_TREE_BYTES - total)
                total += len(data)
                digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    visit(root)
    return digest.hexdigest(), entries


def _copy_tree(source: Path, destination: Path) -> str:
    digest, entries = _scan(source)
    _safe_path(destination, exists=False)
    destination.mkdir(mode=0o700)
    for relative, mode in entries:
        src, dst = source / relative, destination / relative
        if src.is_dir():
            dst.mkdir(mode=0o700)
        else:
            with dst.open("xb") as stream:
                stream.write(_read_bytes(src, MAX_TREE_BYTES))
            dst.chmod(mode)
    # Set directory modes only after children have been copied.
    for relative, mode in reversed(entries):
        if (destination / relative).is_dir():
            (destination / relative).chmod(mode)
    destination.chmod(stat.S_IMODE(source.stat().st_mode))
    if _scan(source)[0] != digest or _scan(destination)[0] != digest:
        raise ValueError("Plugin content changed while creating a snapshot")
    return digest


def _plugin(path: Path, store: Path) -> Path:
    path = _safe_path(path)
    if path == store.parent or not path.is_relative_to(store.parent):
        raise ValueError("Plugin must be an output directory inside the store's project")
    if path.is_relative_to(store) or store.is_relative_to(path) or (path / ".git").exists():
        raise ValueError("Plugin cannot overlap evidence store or be a repository root")
    if not path.is_dir():
        raise ValueError("Plugin must be a directory")
    return path


def _pair(baseline: Path, candidate: Path, store: Path) -> tuple[Path, Path]:
    baseline, candidate = _plugin(baseline, store), _plugin(candidate, store)
    if baseline.is_relative_to(candidate) or candidate.is_relative_to(baseline):
        raise ValueError("Baseline and candidate must be separate output directories")
    return baseline, candidate


def record_event(store: Path, event: dict) -> dict:
    """Record supplied observations; lesson proposals remain inert, reviewable text."""
    store = _store(store)
    if (not isinstance(event, dict) or not isinstance(event.get("kind"), str)
            or event["kind"] not in {"outcome", "failure", "correction"}):
        raise ValueError("Event kind must be outcome, failure, or correction")
    summary = _text(event.get("summary"), "summary", 4000)
    evidence = event.get("evidence")
    if isinstance(evidence, dict) and set(evidence) == {"path"}:
        supplied = Path(_text(evidence["path"], "evidence path", 4000))
        path = _safe_path(supplied if supplied.is_absolute() else store.parent / supplied)
        if not path.is_relative_to(store.parent) or path.is_relative_to(store):
            raise ValueError("Evidence file must be inside the project outside its store")
        data = _read_bytes(path, MAX_TREE_BYTES)
        evidence = {"path": path.relative_to(store.parent).as_posix(),
                    "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    else:
        evidence = _text(evidence, "evidence", 16_000)
    record = {"id": str(uuid.uuid4()), "type": "event", "created_at": _now(),
              "kind": event["kind"], "summary": summary, "evidence": evidence}
    if "proposed_lesson" in event:
        record["proposed_lesson"] = _text(event["proposed_lesson"], "proposed_lesson", 4000)
    with _lock(store):
        _capacity(store / "events", MAX_EVENTS)
        _write_json(store / "events" / f"{record['id']}.json", _sign(store, record))
    return record


def lessons(store: Path) -> list[dict]:
    """Group explicitly supplied proposals and evidence IDs without executing them."""
    path = _safe_path(store, exists=False)
    if not path.exists():
        return []
    store = _store(store, create=False)
    grouped: dict[str, dict] = {}
    with _lock(store):
        if sum(1 for _ in (store / "events").iterdir()) > MAX_EVENTS:
            raise ValueError("Local event limit exceeded")
        for path in sorted((store / "events").glob("*.json")):
            event = _verified(store, path)
            text = event.get("proposed_lesson")
            if text is None:
                continue
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            lesson = grouped.setdefault(key, {"id": key, "status": "proposed", "text": text,
                                               "evidence_ids": [], "occurrences": 0})
            lesson["evidence_ids"].append(event["id"])
            lesson["occurrences"] += 1
    return list(grouped.values())


def _suite(path: Path, store: Path) -> tuple[list[dict], dict]:
    path = _safe_path(path)
    if not path.is_relative_to(store.parent):
        raise ValueError("Suite must be a project-local file")
    raw = _read_bytes(path)
    value = json.loads(raw)
    cases = value.get("cases") if isinstance(value, dict) else value
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError(f"Suite must contain 1..{MAX_CASES} cases")
    normalized, seen = [], set()
    for case in cases:
        if not isinstance(case, dict) or set(case) - {"id", "argv", "expected_exit", "timeout", "weight", "critical"}:
            raise ValueError("Invalid suite case fields")
        name = _text(case.get("id"), "case id", 200)
        argv = case.get("argv")
        if name in seen or not isinstance(argv, list) or not 1 <= len(argv) <= 100:
            raise ValueError("Case IDs must be unique and argv must be nonempty")
        if any(not isinstance(arg, str) or "\0" in arg or len(arg.encode("utf-8")) > 16_000 for arg in argv) or not argv[0]:
            raise ValueError("Invalid command argument")
        expected = case.get("expected_exit", 0)
        timeout, weight = case.get("timeout", 30), case.get("weight", 1)
        critical = case.get("critical", True)
        if type(expected) is not int or not -255 <= expected <= 255 or type(critical) is not bool:
            raise ValueError("Invalid expected exit or critical flag")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError("Timeout must be positive and at most 60 seconds")
        if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 < weight <= 1000:
            raise ValueError("Weight must be positive and at most 1000")
        normalized.append({"id": name, "argv": argv, "expected_exit": expected,
                           "timeout": float(timeout), "weight": float(weight), "critical": critical})
        seen.add(name)
    if sum(case["timeout"] for case in normalized) > 300:
        raise ValueError("Suite total timeout exceeds 300 seconds per side")
    return normalized, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "identity": _identity(path)}


def _run(case: dict, cwd: Path) -> dict:
    started = time.monotonic()
    output = bytearray()
    result = {"id": case["id"], "expected_exit": case["expected_exit"],
              "weight": case["weight"], "critical": case["critical"],
              "exit_code": None, "timed_out": False, "output_limited": False}
    try:
        process = subprocess.Popen(case["argv"], cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   shell=False, start_new_session=True)
    except OSError as exc:
        result["error"] = str(exc)[:1000]
    else:
        def stop() -> None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Some host sandboxes forbid group signals while allowing the
                # direct child to be killed. Fail the case rather than claiming
                # that descendants were contained/reaped.
                result["cleanup_limited"] = True
                try:
                    process.kill()
                except (ProcessLookupError, PermissionError):
                    pass
        assert process.stdout is not None
        try:
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = case["timeout"] - (time.monotonic() - started)
                    if remaining <= 0:
                        result["timed_out"] = True
                        stop(); break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        capacity = MAX_OUTPUT_BYTES - len(output)
                        output.extend(chunk[:capacity])
                        if len(chunk) > capacity:
                            result["output_limited"] = True
                            stop(); break
                    if result["output_limited"]:
                        break
            remaining = max(0.001, case["timeout"] - (time.monotonic() - started))
            try:
                result["exit_code"] = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                stop(); result["exit_code"] = process.wait(timeout=2)
        finally:
            stop()  # also reap children left in the command's process group
            if process.poll() is None:
                process.wait(timeout=2)
            process.stdout.close()
    result["duration_seconds"] = round(time.monotonic() - started, 6)
    result["output"] = output[:MAX_OUTPUT_PREVIEW].decode("utf-8", errors="replace")
    result["output_bytes"] = len(output)
    result["preview_truncated"] = len(output) > MAX_OUTPUT_PREVIEW
    result["output_sha256"] = hashlib.sha256(output).hexdigest()
    result["passed"] = (result["exit_code"] == case["expected_exit"] and not result["timed_out"]
                        and not result["output_limited"] and not result.get("cleanup_limited") and "error" not in result)
    return result


def _decision(record: dict) -> dict:
    before, after = record["results"]["baseline"], record["results"]["candidate"]
    improved = [b["id"] for b, c in zip(before, after) if not b["passed"] and c["passed"]]
    regressions = [b["id"] for b, c in zip(before, after) if b["passed"] and not c["passed"]]
    score_before = sum(case["weight"] for case in before if case["passed"])
    score_after = sum(case["weight"] for case in after if case["passed"])
    return {"baseline_score": score_before, "candidate_score": score_after,
            "improved_cases": improved, "regressions": regressions,
            "eligible": bool(record["unchanged"] and all(case["passed"] for case in after)
                             and improved and not regressions and score_after > score_before)}


def _check_binding(binding: dict, path: Path, *, tree: bool) -> None:
    path = _safe_path(path)
    if str(path) != binding["path"] or _identity(path) != binding["identity"]:
        raise ValueError("Artifact identity changed after evaluation")
    digest = _scan(path)[0] if tree else hashlib.sha256(_read_bytes(path)).hexdigest()
    if digest != binding["sha256"]:
        raise ValueError("Artifact content changed after evaluation")


def evaluate(baseline: Path, candidate: Path, suite: Path, store: Path,
             allow_exec: bool = False) -> dict:
    """Run bounded commands against snapshots and persist authenticated evidence."""
    if allow_exec is not True:
        raise PermissionError("Evaluation runs commands; explicit allow_exec=True is required")
    store = _store(store)
    baseline, candidate = _pair(baseline, candidate, store)
    cases, suite_binding = _suite(suite, store)
    identifier = str(uuid.uuid4())
    directory = store / "evaluations" / identifier
    with _lock(store):
        _capacity(store / "evaluations", MAX_EVALUATIONS)
        directory.mkdir(mode=0o700)
        bindings = {}
        for name, source in (("baseline", baseline), ("candidate", candidate)):
            identity = _identity(source)
            digest = _copy_tree(source, directory / name)
            if _identity(source) != identity:
                raise ValueError("Artifact identity changed during snapshot")
            bindings[name] = {"path": str(source), "identity": identity, "sha256": digest}
    results = {name: [_run(case, directory / name) for case in cases] for name in ("baseline", "candidate")}
    unchanged, errors = True, []
    for name in ("baseline", "candidate"):
        try:
            _check_binding(bindings[name], Path(bindings[name]["path"]), tree=True)
            if _scan(directory / name)[0] != bindings[name]["sha256"]:
                raise ValueError(f"{name} snapshot was changed by a command")
        except (ValueError, OSError) as exc:
            unchanged = False; errors.append(str(exc))
    try:
        _check_binding(suite_binding, Path(suite_binding["path"]), tree=False)
    except (ValueError, OSError) as exc:
        unchanged = False; errors.append(str(exc))
    record = {"id": identifier, "type": "evaluation", "runner_version": 1,
              "created_at": _now(), "artifacts": bindings, "suite": suite_binding,
              "cases": cases, "results": results, "unchanged": unchanged, "errors": errors}
    record["decision"] = _decision(record)
    with _lock(store):
        _write_json(directory / "record.json", _sign(store, record))
    return record


def _replace(baseline: Path, staging: Path, old: Path, commit) -> None:
    """Keep the original on disk until a durable record confirms replacement."""
    os.replace(baseline, old)
    try:
        os.replace(staging, baseline)
        commit()
    except BaseException:
        if baseline.exists():
            os.replace(baseline, staging)
        os.replace(old, baseline)
        raise
    shutil.rmtree(old)


def promote(baseline: Path, candidate: Path, store: Path, evaluation_id: str) -> dict:
    """Promote only recorded strict improvement; retain a verified rollback copy."""
    store = _store(store)
    baseline, candidate = _pair(baseline, candidate, store)
    evaluation_id = _id(evaluation_id)
    with _lock(store):
        evaluation_dir = store / "evaluations" / evaluation_id
        record = _verified(store, evaluation_dir / "record.json")
        if record.get("type") != "evaluation" or record.get("id") != evaluation_id or record.get("runner_version") != 1:
            raise ValueError("Invalid evaluation record")
        for name, path in (("baseline", baseline), ("candidate", candidate)):
            _check_binding(record["artifacts"][name], path, tree=True)
            if _scan(evaluation_dir / name)[0] != record["artifacts"][name]["sha256"]:
                raise ValueError("Evaluation snapshot integrity check failed")
        _check_binding(record["suite"], Path(record["suite"]["path"]), tree=False)
        if not _decision(record)["eligible"]:
            raise ValueError("Candidate must pass all cases with strict improvement and no regressions")
        identifier = str(uuid.uuid4())
        directory = store / "promotions" / identifier
        _capacity(store / "promotions", MAX_PROMOTIONS)
        directory.mkdir(mode=0o700)
        before = _copy_tree(baseline, directory / "backup")
        staging = baseline.parent / f".{baseline.name}.promote-{identifier}"
        old = baseline.parent / f".{baseline.name}.previous-{identifier}"
        after = _copy_tree(evaluation_dir / "candidate", staging)
        for name, path in (("baseline", baseline), ("candidate", candidate)):
            _check_binding(record["artifacts"][name], path, tree=True)
        _check_binding(record["suite"], Path(record["suite"]["path"]), tree=False)
        promotion = {"id": identifier, "type": "promotion", "created_at": _now(),
                     "baseline": str(baseline), "evaluation_id": evaluation_id,
                     "before_sha256": before, "after_sha256": after,
                     "before_identity": record["artifacts"]["baseline"]["identity"]}
        def commit() -> None:
            if _scan(baseline)[0] != after:
                raise ValueError("Promoted content failed verification")
            promotion["after_identity"] = _identity(baseline)
            _write_json(directory / "record.json", _sign(store, promotion))
        _replace(baseline, staging, old, commit)
        return promotion


def rollback(baseline: Path, store: Path, promotion_id: str) -> dict:
    """Restore one verified backup only while its promoted baseline is unchanged."""
    store = _store(store)
    baseline = _plugin(baseline, store)
    promotion_id = _id(promotion_id)
    with _lock(store):
        directory = store / "promotions" / promotion_id
        promotion = _verified(store, directory / "record.json")
        if promotion.get("type") != "promotion" or promotion.get("id") != promotion_id:
            raise ValueError("Invalid promotion record")
        binding = {"path": promotion["baseline"], "sha256": promotion["after_sha256"],
                   "identity": promotion["after_identity"]}
        _check_binding(binding, baseline, tree=True)
        if _scan(directory / "backup")[0] != promotion["before_sha256"]:
            raise ValueError("Rollback backup integrity check failed")
        identifier = str(uuid.uuid4())
        _capacity(store / "rollbacks", MAX_PROMOTIONS)
        staging = baseline.parent / f".{baseline.name}.rollback-{identifier}"
        old = baseline.parent / f".{baseline.name}.replaced-{identifier}"
        restored = _copy_tree(directory / "backup", staging)
        _check_binding(binding, baseline, tree=True)
        result = {"id": identifier, "type": "rollback", "created_at": _now(),
                  "promotion_id": promotion_id, "baseline": str(baseline), "sha256": restored}
        def commit() -> None:
            if _scan(baseline)[0] != restored:
                raise ValueError("Restored content failed verification")
            result["identity"] = _identity(baseline)
            _write_json(store / "rollbacks" / f"{identifier}.json", _sign(store, result))
        _replace(baseline, staging, old, commit)
        return result

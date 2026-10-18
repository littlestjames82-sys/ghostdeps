"""Manifest and lockfile parsers. Local files only, stdlib only.

Every parser returns declarations and/or pins as recorded in the file.
A file that exists but is not parsed in v0.1 (or whose shape is not
recognised) is reported as detected-not-parsed, never silently skipped
and never guessed at.

Declaration: {name, spec, kind, group, source}
  kind: runtime | dev | optional | peer | build
  spec: the requirement text exactly as recorded ("" when none recorded)
Pin: {name, version, source, via, direct}
  via: lockfile | manifest (an exact pin written in a manifest)
  direct: True for pins of packages the project itself declares (for a
  lockfile, the packages named in the lockfile's root entry); False for
  transitive lockfile pins, which are recorded compactly downstream.

Build requirements are NOT declarations: a pyproject [build-system]
requires list is build tooling, kept on SourceResult.build_requirements
and surfaced separately so it never enters the declared/imported
comparison (PLAN scopes pyproject parsing to project dependencies,
optional-dependencies, and dependency-groups).
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from .names import norm

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".tox", ".nox", "target", "dist", "build", ".eggs", "vendor",
    ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
}

# Files GhostDeps knows how to detect. Parsed in v0.1: pyproject.toml,
# requirements*.txt, package.json, package-lock.json (summary), Cargo.toml.
DETECTED_ONLY_NAMES = {
    "gemfile": "Ruby (Gemfile)",
    "go.mod": "Go (go.mod)",
    "composer.json": "PHP (composer.json)",
    "pipfile": "Python (Pipfile)",
    "setup.py": "Python (setup.py, imperative: not parsed)",
    "setup.cfg": "Python (setup.cfg)",
    "yarn.lock": "JS (yarn.lock: summary parse not in v0.1)",
    "pnpm-lock.yaml": "JS (pnpm-lock.yaml: summary parse not in v0.1)",
    "cargo.lock": "Rust (Cargo.lock: full pin list not in v0.1)",
    "poetry.lock": "Python (poetry.lock: full pin list not in v0.1)",
}


class SourceResult:
    def __init__(self, path: Path, rel: str, kind: str, ecosystem: str):
        self.path = path
        self.rel = rel
        self.kind = kind  # manifest | lockfile
        self.ecosystem = ecosystem
        self.status = "parsed"  # parsed | detected-not-parsed | error
        self.note = ""
        self.declarations: list[dict] = []
        self.pins: list[dict] = []
        # Build tooling requirements (pyproject [build-system] requires).
        # Kept apart from declarations on purpose: they are not project
        # dependencies and are never compared against imports.
        self.build_requirements: list[dict] = []

    def record(self) -> dict:
        return {
            "file": self.rel,
            "kind": self.kind,
            "ecosystem": self.ecosystem,
            "status": self.status,
            "note": self.note,
            "build_requirements": self.build_requirements,
        }


def _decl(name, spec, kind, source, group=""):
    return {"name": name, "spec": spec or "", "kind": kind,
            "group": group, "source": source}


# ---------------------------------------------------------------- Python

_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]|[A-Za-z0-9])")


def parse_pep508(text: str) -> tuple[str, str] | None:
    """(name, spec) from a PEP 508 requirement string, as recorded.

    Extras are dropped from the name (Pillow[all] declares Pillow) and
    environment markers are dropped from the spec; the requirement core
    is kept verbatim.
    """
    m = _PEP508_NAME.match(text or "")
    if not m:
        return None
    name = m.group(0).strip()
    rest = text[m.end():].strip()
    if rest.startswith("["):
        end = rest.find("]")
        rest = rest[end + 1:].strip() if end >= 0 else ""
    spec = rest.split(";", 1)[0].strip()
    return name, spec


def _parse_pyproject(path: Path, rel: str) -> SourceResult:
    res = SourceResult(path, rel, "manifest", "python")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # malformed TOML is evidence, not a crash
        res.status = "error"
        res.note = f"TOML parse failed: {exc}"
        return res
    project = data.get("project", {}) or {}
    for req in project.get("dependencies", []) or []:
        parsed = parse_pep508(str(req))
        if parsed:
            res.declarations.append(_decl(parsed[0], parsed[1], "runtime", rel))
    for group, reqs in (project.get("optional-dependencies", {}) or {}).items():
        for req in reqs or []:
            parsed = parse_pep508(str(req))
            if parsed:
                res.declarations.append(
                    _decl(parsed[0], parsed[1], "optional", rel, group=str(group)))
    for group, entries in (data.get("dependency-groups", {}) or {}).items():
        for entry in entries or []:
            if isinstance(entry, str):
                parsed = parse_pep508(entry)
                if parsed:
                    res.declarations.append(
                        _decl(parsed[0], parsed[1], "dev", rel, group=str(group)))
            elif isinstance(entry, dict) and "include-group" in entry:
                res.note = (res.note + " " if res.note else "") + \
                    f"dependency-group '{group}' includes group " \
                    f"'{entry['include-group']}' (recorded, not expanded)"
    # [build-system] requires are build tooling, not project
    # dependencies (PLAN scope). Record them separately; they never
    # enter the declared set or the declared/imported comparison.
    build = data.get("build-system", {}) or {}
    for req in build.get("requires", []) or []:
        parsed = parse_pep508(str(req))
        if parsed:
            res.build_requirements.append(
                _decl(parsed[0], parsed[1], "build", rel))
    poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
    for name, val in (poetry.get("dependencies", {}) or {}).items():
        if str(name).lower() == "python":
            continue
        spec = val.get("version", "") if isinstance(val, dict) else str(val)
        res.declarations.append(_decl(str(name), str(spec), "runtime", rel))
    for name, val in (poetry.get("dev-dependencies", {}) or {}).items():
        spec = val.get("version", "") if isinstance(val, dict) else str(val)
        res.declarations.append(_decl(str(name), str(spec), "dev", rel))
    return res


_REQ_SKIP_PREFIXES = ("-", "--hash")
_REQ_INCLUDE = re.compile(r"^(?:-r|--requirement)\s+(\S+)")


def _parse_requirements(path: Path, rel: str) -> SourceResult:
    res = SourceResult(path, rel, "manifest", "python")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        res.status = "error"
        res.note = f"read failed: {exc}"
        return res
    includes = []
    unparsed = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        inc = _REQ_INCLUDE.match(line)
        if inc:
            includes.append(inc.group(1))
            continue
        # Strip trailing comments (a # preceded by whitespace) and markers.
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line or line.startswith(_REQ_SKIP_PREFIXES):
            if line.startswith("-e") and "#egg=" in line:
                egg = line.split("#egg=", 1)[1].split("&")[0].strip()
                if egg:
                    res.declarations.append(_decl(egg, "editable", "runtime", rel))
                    continue
            if line:
                unparsed += 1
            continue
        parsed = parse_pep508(line)
        if parsed:
            res.declarations.append(_decl(parsed[0], parsed[1], "runtime", rel))
        else:
            unparsed += 1
    notes = []
    if includes:
        notes.append("includes noted, not followed: " + ", ".join(includes))
    if unparsed:
        notes.append(f"{unparsed} line(s) not in requirement shape, skipped")
    res.note = "; ".join(notes)
    return res


# -------------------------------------------------------------------- JS

def _parse_package_json(path: Path, rel: str) -> SourceResult:
    res = SourceResult(path, rel, "manifest", "javascript")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        res.status = "error"
        res.note = f"JSON parse failed: {exc}"
        return res
    sections = [
        ("dependencies", "runtime"),
        ("devDependencies", "dev"),
        ("peerDependencies", "peer"),
        ("optionalDependencies", "optional"),
    ]
    for key, kind in sections:
        for name, spec in (data.get(key, {}) or {}).items():
            res.declarations.append(_decl(str(name), str(spec), kind, rel))
    return res


def _parse_package_lock(path: Path, rel: str) -> SourceResult:
    """Summary-level only: root pins and top-level package pins.

    Every top-level pin is recorded, split into direct pins (packages
    the lockfile's root entry itself declares) and transitive pins
    (everything else installed at the top level). Nested entries are
    counted, not reproduced.
    """
    res = SourceResult(path, rel, "lockfile", "javascript")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        res.status = "error"
        res.note = f"JSON parse failed: {exc}"
        return res
    packages = data.get("packages")
    total = 0
    if isinstance(packages, dict) and packages:
        root = packages.get("", {}) or {}
        direct_names = set()
        for key in ("dependencies", "devDependencies",
                    "peerDependencies", "optionalDependencies"):
            for dep_name in (root.get(key, {}) or {}):
                direct_names.add(norm(dep_name))
        for pkg_path, info in packages.items():
            if not pkg_path or not isinstance(info, dict):
                continue
            total += 1
            if pkg_path.count("node_modules") > 1:
                continue  # nested: counted only, not recorded as a pin
            name = pkg_path.split("node_modules/")[-1]
            version = info.get("version")
            if name and version:
                res.pins.append({"name": name, "version": str(version),
                                 "source": rel, "via": "lockfile",
                                 "direct": norm(name) in direct_names})
        n_direct = sum(1 for p in res.pins if p["direct"])
        res.note = (f"lockfileVersion {data.get('lockfileVersion', '?')}; "
                    f"summary level: {n_direct} direct pin(s), "
                    f"{len(res.pins) - n_direct} transitive pin(s), "
                    f"{total} package entries total")
    elif isinstance(data.get("dependencies"), dict):
        # Legacy (lockfileVersion 1) shape is a flat top-level map with
        # no root entry to split direct from transitive against; its
        # entries are recorded as direct pins, as before.
        for name, info in data["dependencies"].items():
            if isinstance(info, dict) and info.get("version"):
                res.pins.append({"name": name, "version": str(info["version"]),
                                 "source": rel, "via": "lockfile",
                                 "direct": True})
        res.note = (f"lockfileVersion {data.get('lockfileVersion', '?')} "
                    f"(legacy shape); summary level")
    else:
        res.status = "detected-not-parsed"
        res.note = "lockfile shape not recognised; no pins extracted"
    return res


# ------------------------------------------------------------------ Rust

def _cargo_section(res: SourceResult, table, kind: str, rel: str):
    for name, val in (table or {}).items():
        if isinstance(val, dict):
            if "version" in val:
                spec = str(val["version"])
            elif "path" in val:
                spec = f"path: {val['path']}"
            elif "git" in val:
                spec = f"git: {val['git']}"
            else:
                spec = ""
        else:
            spec = str(val)
        res.declarations.append(_decl(str(name), spec, kind, rel))


def _parse_cargo_toml(path: Path, rel: str) -> SourceResult:
    res = SourceResult(path, rel, "manifest", "rust")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        res.status = "error"
        res.note = f"TOML parse failed: {exc}"
        return res
    pkg = data.get("package", {}) or {}
    _cargo_section(res, data.get("dependencies"), "runtime", rel)
    _cargo_section(res, data.get("dev-dependencies"), "dev", rel)
    _cargo_section(res, data.get("build-dependencies"), "build", rel)
    for target in (data.get("target", {}) or {}).values():
        if isinstance(target, dict):
            _cargo_section(res, target.get("dependencies"), "runtime", rel)
    workspace = data.get("workspace", {}) or {}
    _cargo_section(res, workspace.get("dependencies"), "runtime", rel)
    members = workspace.get("members")
    if members:
        res.note = f"workspace with {len(members)} member(s) noted: " + \
            ", ".join(str(m) for m in members)
    if pkg.get("name"):
        res.note = (res.note + "; " if res.note else "") + \
            f"package name as recorded: {pkg['name']}"
    return res


# -------------------------------------------------------------- discovery

_PARSERS = {
    "pyproject.toml": _parse_pyproject,
    "package.json": _parse_package_json,
    "package-lock.json": _parse_package_lock,
    "cargo.toml": _parse_cargo_toml,
}


def _is_requirements(name: str) -> bool:
    low = name.lower()
    return low.startswith("requirements") and low.endswith(".txt")


def discover_sources(root: Path) -> list[SourceResult]:
    """Find and parse every manifest/lockfile in the tree.

    Root-level manifests are the primary evidence. Manifests deeper in
    the tree are parsed too (monorepos, examples) with their relative
    path recorded as the source; node_modules and VCS dirs are skipped.
    """
    results: list[SourceResult] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in sorted(filenames):
            low = fname.lower()
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(root))
            parser = _PARSERS.get(fname) or _PARSERS.get(low)
            if parser is not None:
                results.append(parser(fpath, rel))
            elif _is_requirements(fname):
                results.append(_parse_requirements(fpath, rel))
            elif low in DETECTED_ONLY_NAMES:
                res = SourceResult(fpath, rel, "manifest",
                                   DETECTED_ONLY_NAMES[low].split(" ")[0].lower())
                res.status = "detected-not-parsed"
                res.note = DETECTED_ONLY_NAMES[low] + ": detected, not parsed in v0.1"
                if low.endswith(".lock"):
                    res.kind = "lockfile"
                results.append(res)
    results.sort(key=lambda r: (r.rel.count("/"), r.rel))
    return results

"""GhostDeps CLI: pack / doctor.

Evidence only: no command here installs, removes, edits a manifest, or
touches the network. Exit codes: 0 = pack built, no imported-not-declared
findings; 1 = findings present (imported with no declaration found);
2 = could not build (project missing, or no manifest parsed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .imports_scan import scan_imports
from .manifests import discover_sources
from .xref import build_pack, render_deps_md


def _analyse(project: Path):
    sources = discover_sources(project)
    parsed = [s for s in sources if s.status == "parsed"]
    idx = scan_imports(project)
    pack = build_pack(str(project), sources, idx)
    return sources, parsed, pack


def cmd_pack(args) -> int:
    project = Path(args.project)
    if not project.is_dir():
        print(f"ghostdeps: project not found: {project}", file=sys.stderr)
        return 2
    sources, parsed, pack = _analyse(project)
    if not parsed:
        print("ghostdeps: no manifest or lockfile could be parsed in "
              f"{project}; nothing to pack", file=sys.stderr)
        for s in sources:
            print(f"  {s.rel}: {s.status} ({s.note})", file=sys.stderr)
        return 2
    out = Path(args.output) if args.output else project / "deps-pack"
    out.mkdir(parents=True, exist_ok=True)
    md = render_deps_md(pack)
    (out / "DEPS.md").write_text(md, encoding="utf-8")
    (out / "deps-manifest.json").write_text(
        json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    c = pack["counts"]
    print(f"GhostDeps pack: {project}")
    print(f"  declared {c['declared']} | declared and imported "
          f"{c['declared_and_used']} | declared, no import found "
          f"{c['declared_not_imported']} | imported, no declaration found "
          f"{c['imported_not_declared']} | pins recorded {c['pins_recorded']}")
    print(f"  wrote {out / 'DEPS.md'}")
    print(f"  wrote {out / 'deps-manifest.json'}")
    if args.json:
        print(json.dumps(pack, indent=2, sort_keys=True))
    return 1 if c["imported_not_declared"] else 0


def cmd_doctor(args) -> int:
    project = Path(args.project)
    if not project.is_dir():
        print(f"ghostdeps: project not found: {project}", file=sys.stderr)
        return 2
    sources, parsed, pack = _analyse(project)
    if args.json:
        print(json.dumps({
            "project": str(project),
            "sources": pack["sources"],
            "source_groups": pack.get("source_groups", []),
            "files_scanned": pack["files_scanned"],
            "import_scan": pack.get("import_scan", {}),
            "parser_confidence": pack["parser_confidence"],
            "counts": pack["counts"],
        }, indent=2, sort_keys=True))
    else:
        print(f"GhostDeps doctor: {project}")
        for s in pack["sources"]:
            note = f" - {s['note']}" if s["note"] else ""
            print(f"  {s['file']} ({s['kind']}, {s['ecosystem']}): "
                  f"{s['status']}{note}")
        if not pack["sources"]:
            print("  no manifests or lockfiles found")
        fs = pack["files_scanned"]
        print(f"  files scanned: python {fs['python']}, "
              f"javascript {fs['javascript']}, rust {fs['rust']}")
        import_scan = pack.get("import_scan", {})
        if import_scan.get("ignored_file_count"):
            print(f"  import files ignored by local ignore rules: "
                  f"{import_scan['ignored_file_count']}")
        for warning in import_scan.get("warnings", []):
            print(f"  ignore warning: {warning}")
        for eco, label in pack["parser_confidence"].items():
            print(f"  {eco} import extraction: {label}")
        if pack["python_parse_failures"]:
            print(f"  python files that failed to parse: "
                  f"{len(pack['python_parse_failures'])} "
                  f"({', '.join(pack['python_parse_failures'][:5])})")
        c = pack["counts"]
        print(f"  declared {c['declared']}, imported-not-declared "
              f"{c['imported_not_declared']} (finding), "
              f"declared-not-imported {c['declared_not_imported']} (information)")
    if not parsed:
        return 2
    return 1 if pack["counts"]["imported_not_declared"] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ghostdeps",
        description="Dependency context pack from local manifests, "
                    "lockfiles, and imports. Local evidence only, "
                    "no network, no verdicts.")
    parser.add_argument("--version", action="version",
                        version=f"ghostdeps {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pack = sub.add_parser("pack", help="build DEPS.md + deps-manifest.json")
    p_pack.add_argument("project")
    p_pack.add_argument("-o", "--output",
                        help="output directory (default: <project>/deps-pack)")
    p_pack.add_argument("--json", action="store_true",
                        help="also print the manifest JSON to stdout")
    p_pack.set_defaults(func=cmd_pack)

    p_doc = sub.add_parser("doctor", help="report what was found and parsed")
    p_doc.add_argument("project")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

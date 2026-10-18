# GhostDeps

GhostDeps packs what a project depends on into a short brief an agent
(or a person) can read before touching the code: what the project
declares, what the code actually imports, where those two lists
disagree, and which versions the local files record.

It is the dependency companion to GhostSlice. Slice packs the code;
Deps packs the dependency facts around the code; the agent starts a
session knowing both.

## What it does

Point it at a project:

```
ghostdeps pack path/to/project -o out
ghostdeps doctor path/to/project
```

`pack` writes two files:

- `DEPS.md`, a human report with four sections: declared and used,
  declared not imported, imported not declared, and pins as recorded.
- `deps-manifest.json`, the same facts in machine-readable form, with
  the evidence for each entry: which manifest declares it, which
  lockfile pins it, and which source files import it. Manifest
  evidence is also grouped by holding directory in `source_groups`,
  which makes nested monorepo sources easier to attribute.

It reads, per ecosystem:

- Python: `pyproject.toml` (project dependencies, optional
  dependencies, dependency groups) and `requirements*.txt` files.
  `setup.py` and `setup.cfg` are recorded as present but not parsed.
- JavaScript and TypeScript: `package.json` (dependencies,
  devDependencies, peerDependencies, optionalDependencies) and
  `package-lock.json` at summary level: lockfile version and root
  package pins, never the whole file.
- Rust: `Cargo.toml` dependency tables, with workspace members noted.
- Anything else it recognises (Gemfile, go.mod, composer.json,
  Pipfile) is listed as detected, not parsed. Nothing is silently
  skipped.

Imports are extracted from the code and cross-referenced against the
declarations. Python imports come from the real `ast` parser.
JavaScript and TypeScript imports come from pattern matching over
`import`, `export ... from`, and `require()` statements, and every
report labels that side parser-lite. Import names are matched to
distribution names with a small built-in table for the common
mismatches (PIL is Pillow, yaml is PyYAML, cv2 is opencv-python,
sklearn is scikit-learn) plus normalisation of case and separators.
Standard library modules and files inside the project itself are
classified separately and are never reported as missing declarations.
The import scanner honours `.gitignore` files in the tree and the
`ignore` list in a root `.ghostslice.json`; files it skips under those
rules are listed in the manifest under `import_scan`. Manifest and
lockfile discovery is not filtered by those rules, so dependency
evidence is not hidden just because a source directory is ignored.

## The trust rules

- **Local only, no network, ever.** There is no code path that opens
  a socket. No registry lookups, no advisory lookups, no update
  checks.
- **Evidence only.** A declared package with no import found is
  reported in exactly those words, with the common innocent
  explanations listed next to it (command line entry points, plugins,
  type-only or runtime loading). An imported package with no
  declaration names the files that import it and notes the common
  innocent causes. GhostDeps never grades a dependency, never scores
  a project, and never tells you to add or remove anything. A human
  decides what a gap means.
- **Versions as recorded.** A version in the report is the version
  written in your local manifest or lockfile. Nothing is compared
  against anything outside the tree.
- **Read-only.** GhostDeps never edits a manifest, never installs or
  removes a package, and never rewrites a lockfile.

## Exit codes

For `doctor` (and CI use):

- 0: the pack built and there are no findings.
- 1: findings present. Imported-not-declared is a finding. Declared
  but not imported is information, not a failure.
- 2: could not build a pack, because no manifests were found or a
  manifest failed to parse. A project with no manifests is reported
  as having none; that is an answer, not a crash.

## Honest limits

- The import-name to distribution-name mapping is a heuristic table
  plus normalisation. It will miss unusual cases, and unmapped names
  are compared as written.
- Declared-not-imported is a list of leads, not a verdict. Plugins,
  entry points, and dynamically loaded packages all land there with
  no import evidence, which is why the wording stays neutral.
- Parser-lite JavaScript extraction can miss re-exports and computed
  requires. Comment stripping is string-aware, so comment markers in
  quoted strings, template literals, and regex literals do not erase
  the code that follows them, but this is still not a full JavaScript
  parser. It is labelled parser-lite everywhere it is used.
- Lockfiles are read at summary level only. There is no transitive
  resolution, no dependency tree, and no deduplication analysis in
  this version.
- Ecosystems beyond Python, JavaScript/TypeScript, and Rust are
  detected and listed, not parsed, in v0.1.
- Monorepo workspaces are noted, not resolved; sibling packages can
  make an import look undeclared when it is satisfied inside the
  repo, and the report says so next to the entry. Nested manifest
  evidence is grouped by directory, but the cross-reference remains
  project-wide rather than being split per package.

## Layout of a pack

A pack is two files and travels on its own or next to a GhostSlice
bundle. It does not modify Slice bundles, and Slice does not need to
know it exists.

## Status

v0.1, local CLI first. Zero-dependency Python standard library, same
as the rest of the GhostSlice companion suite.

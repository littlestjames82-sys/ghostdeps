"""GhostDeps: dependency context pack from local evidence only.

Parses project manifests and lockfiles present in the tree and
cross-references declared dependencies against what the code actually
imports. No network, no vulnerability verdicts, no version judgements:
versions are reported as recorded in local files.
"""

__version__ = "0.1.0"

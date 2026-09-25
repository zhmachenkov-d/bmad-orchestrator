"""Shared orch library: registry, stories, markers, claims, sprint status, contract detectors, gate.

Every orch skill reaches this code through `scripts/orch.py`; the CLI is the contract.
"""

SCHEMA_VERSION = 1


class OrchError(Exception):
    """A usage or environment error (exit code 2), as opposed to a failing verdict (exit code 1)."""

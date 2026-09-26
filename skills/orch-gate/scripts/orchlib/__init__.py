"""Shared orch library: registry, stories, markers, claims, sprint status, contract detectors, gate.

Every orch skill reaches this code through `scripts/orch.py`; the CLI is the contract.
"""

SCHEMA_VERSION = 1


class OrchError(Exception):
    """A usage or environment error (exit code 2), as opposed to a failing verdict (exit code 1).

    `code` names the error for callers that branch on it (a skill chooses its recovery by code, never by message);
    `fields` are extra JSON keys that recovery needs, such as the repo to clone.
    """

    def __init__(self, message: str, code: str | None = None, **fields):
        super().__init__(message)
        self.code = code
        self.fields = fields

"""Credential minting — `schemas.md` §1.4, FR-14.

Two modules, split along the line that matters: `policy` is the pure table
that makes L1, L2 and L3 different from each other, and `factory` is how a
credential is obtained once the table has decided. Read `policy` first.
"""

from __future__ import annotations

from .factory import (
    CredentialFactory,
    CredentialMinter,
    GoogleCredentialMinter,
    MintedCredential,
)
from .policy import POLICY, UNCREDENTIALED, CredentialPolicyViolation, mode_for

__all__ = [
    "POLICY",
    "UNCREDENTIALED",
    "CredentialFactory",
    "CredentialMinter",
    "CredentialPolicyViolation",
    "GoogleCredentialMinter",
    "MintedCredential",
    "mode_for",
]

"""Estate generation.

Plants eight flags into a synthetic estate and emits the fixture manifest the
verifier reads (`schemas.md` §3). Deterministic: the seed decides every value
and every host row, so two applies from one seed produce the same estate.

The module is pure — it decides, it does not write. Pushing a plan to GCP is a
separate concern with its own client libraries, which keeps the determinism
criterion testable without a project, credentials or a network.

Read `build.py` first; it is where placement is decided, and placement is what
makes an L3 `held` attributable to one control.
"""

from .build import GenerationResult, build
from .config import GeneratorConfig
from .manifest import FixtureManifest, FlagFixture
from .plan import EstatePlan

__all__ = [
    "EstatePlan",
    "FixtureManifest",
    "FlagFixture",
    "GenerationResult",
    "GeneratorConfig",
    "build",
]

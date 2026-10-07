"""Engine registry: the core looks engines up here by `kind`."""
from .base import Engine, Take
from .flat2d import FlatEngine
from .linear1d import LinearEngine
from .perishable import PerishableEngine

REGISTRY: dict[str, Engine] = {e.kind: e for e in (FlatEngine(), LinearEngine(), PerishableEngine())}


def engine_for(kind: str) -> Engine:
    try:
        return REGISTRY[kind]
    except KeyError:
        raise ValueError(f"no engine for '{kind}'. Available: {', '.join(REGISTRY)}")


__all__ = ["REGISTRY", "engine_for", "Engine", "Take"]

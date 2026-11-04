from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent
MANUALS_ROOT = SOURCE_ROOT / "manuals"
SOURCE_REGISTRY_PATH = SOURCE_ROOT / "source_registry.json"

__all__ = ["SOURCE_ROOT", "MANUALS_ROOT", "SOURCE_REGISTRY_PATH"]


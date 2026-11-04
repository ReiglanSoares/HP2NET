import os
from pathlib import Path


DATA_ROOT = Path(
    os.getenv(
        "PHYLOFLOW_WORKSPACE",
        os.getenv("PHYLOFLOW_AI_DATA_DIR", "phyloflow_workspace"),
    )
)
GENERATED_ROOT = DATA_ROOT / "generated"
PREVIEW_ROOT = DATA_ROOT / "previews"
TRASH_ROOT = DATA_ROOT / "trash"


import json
import re
from datetime import datetime
from pathlib import Path


def normalize_workflow_name(workflow_name):
    normalized = re.sub(r"[^a-z0-9_]+", "_", workflow_name.lower().replace("-", "_"))
    return normalized.strip("_") or "generated_workflow"


def resolve_output_paths(workflow_name, output_directory="generated_workflows"):
    output_root = Path(output_directory)
    metadata_directory = output_root / "metadata"
    output_root.mkdir(parents=True, exist_ok=True)
    metadata_directory.mkdir(parents=True, exist_ok=True)

    base_name = normalize_workflow_name(workflow_name)
    version = 1
    while True:
        suffix = "" if version == 1 else f"_v{version}"
        code_path = output_root / f"{base_name}{suffix}.py"
        metadata_path = metadata_directory / f"{base_name}{suffix}.json"
        if not code_path.exists() and not metadata_path.exists():
            return code_path, metadata_path
        version += 1


def save_generated_workflow(
    generated_code,
    validated_plan,
    model_name,
    validation_checks,
    pattern=None,
    output_directory="generated_workflows"
):
    code_path, metadata_path = resolve_output_paths(
        validated_plan.get("workflow_name", "generated_workflow"),
        output_directory
    )
    code_path.write_text(generated_code.rstrip() + "\n", encoding="utf-8")

    metadata = {
        "workflow_name": normalize_workflow_name(
            validated_plan.get("workflow_name", "generated_workflow")
        ),
        "goal": validated_plan.get("goal"),
        "steps": validated_plan.get("steps", []),
        "model": model_name,
        "validation": "passed",
        "validation_checks": validation_checks,
        "implementation_apps": pattern.get("apps", []) if pattern else [],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds")
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    return code_path, metadata_path

import json
import re
import hashlib
from datetime import datetime
from pathlib import Path

from .paths import GENERATED_ROOT, PREVIEW_ROOT


def normalize_workflow_name(workflow_name):
    normalized = re.sub(r"[^a-z0-9_]+", "_", workflow_name.lower().replace("-", "_"))
    return normalized.strip("_") or "generated_workflow"


def resolve_output_paths(workflow_name, output_directory=GENERATED_ROOT):
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
    output_directory=GENERATED_ROOT
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


def _sha256(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _preview_paths(preview_name, output_directory=PREVIEW_ROOT):
    normalized_name = normalize_workflow_name(preview_name)
    preview_directory = Path(output_directory) / normalized_name
    return {
        "name": normalized_name,
        "directory": preview_directory,
        "metadata": preview_directory / "metadata.json",
        "diff": preview_directory / "changes.diff"
    }


def create_preview_workspace(
    preview_name,
    source_workflow,
    output_directory=PREVIEW_ROOT
):
    paths = _preview_paths(preview_name, output_directory)
    if paths["directory"].exists():
        raise FileExistsError(
            f"Preview '{paths['name']}' already exists. Use --preview {paths['name']}."
        )

    project_path = Path.cwd().resolve()
    source_path = Path(source_workflow).resolve()
    try:
        relative_source = source_path.relative_to(project_path)
    except ValueError as error:
        raise ValueError(f"Preview source must be inside the project: {source_path}") from error
    if source_path.suffix.lower() != ".py" or not source_path.is_file():
        raise ValueError(f"Preview source is not a Python file: {source_path}")

    source_code = source_path.read_text(encoding="utf-8")
    workflow_path = paths["directory"] / source_path.name
    paths["directory"].mkdir(parents=True, exist_ok=False)
    workflow_path.write_text(source_code, encoding="utf-8")
    paths["diff"].write_text("", encoding="utf-8")

    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = {
        "preview_name": paths["name"],
        "source_workflow": str(relative_source),
        "workflow_file": source_path.name,
        "source_sha256": _sha256(source_code),
        "current_preview_sha256": _sha256(source_code),
        "integrations": [],
        "created_at": created_at,
        "updated_at": created_at
    }
    paths["metadata"].write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    paths["workflow"] = workflow_path
    paths["source"] = source_path
    paths["data"] = metadata
    return paths


def load_preview_workspace(
    preview_name,
    output_directory=PREVIEW_ROOT
):
    paths = _preview_paths(preview_name, output_directory)
    if not paths["metadata"].is_file():
        raise FileNotFoundError(
            f"Preview '{paths['name']}' does not exist. Create it with --new-preview."
        )
    try:
        metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid preview metadata: {error}") from error

    if "workflow_file" not in metadata:
        metadata["workflow_file"] = Path(metadata.get("source_workflow", "")).name
    if "source_sha256" not in metadata:
        metadata["source_sha256"] = metadata.get("hashes", {}).get("original_sha256")
    if "current_preview_sha256" not in metadata:
        metadata["current_preview_sha256"] = metadata.get("hashes", {}).get(
            "preview_sha256"
        )
    if "integrations" not in metadata:
        previous_integration = None
        if metadata.get("inserted_function"):
            previous_integration = {
                "generated_source": metadata.get("generated_source"),
                "inserted_function": metadata.get("inserted_function"),
                "goal": metadata.get("goal"),
                "scientific_steps": metadata.get("scientific_steps", []),
                "implementation_apps": metadata.get("implementation_apps", []),
                "model": metadata.get("model"),
                "generation_validation_checks": metadata.get(
                    "generation_validation_checks", {}
                ),
                "integration_validation_checks": metadata.get(
                    "integration_validation_checks", {}
                ),
                "hashes": metadata.get("hashes", {}),
                "created_at": metadata.get("created_at")
            }
        metadata["integrations"] = [previous_integration] if previous_integration else []
    metadata.setdefault("updated_at", metadata.get("created_at"))

    project_path = Path.cwd().resolve()
    source_path = (project_path / metadata.get("source_workflow", "")).resolve()
    preview_directory = paths["directory"].resolve()
    workflow_path = (preview_directory / metadata.get("workflow_file", "")).resolve()
    try:
        source_path.relative_to(project_path)
        workflow_path.relative_to(preview_directory)
    except ValueError as error:
        raise ValueError("Preview metadata contains a path outside the project.") from error
    if not source_path.is_file():
        raise FileNotFoundError(f"Original preview source not found: {source_path}")
    if not workflow_path.is_file():
        raise FileNotFoundError(f"Preview workflow file not found: {workflow_path}")

    current_code = workflow_path.read_text(encoding="utf-8")
    source_hash = metadata.get("source_sha256")
    if source_hash and _sha256(source_path.read_text(encoding="utf-8")) != source_hash:
        raise ValueError(
            "The original workflow changed after this preview was created. "
            "Create a new preview from the current source."
        )
    expected_hash = metadata.get("current_preview_sha256")
    if expected_hash and _sha256(current_code) != expected_hash:
        raise ValueError(
            "Preview workflow was changed outside the integration system. "
            "Update or recreate the preview before continuing."
        )

    paths["workflow"] = workflow_path
    paths["source"] = source_path
    paths["data"] = metadata
    return paths


def update_integration_preview(
    workspace,
    integration_result,
    validated_plan,
    model_name,
    generation_checks,
    pattern=None,
    generated_source=None
):
    workflow_path = workspace["workflow"]
    diff_path = workspace["diff"]
    metadata_path = workspace["metadata"]
    metadata = workspace["data"]

    workflow_temporary = workflow_path.with_suffix(workflow_path.suffix + ".tmp")
    diff_temporary = diff_path.with_suffix(diff_path.suffix + ".tmp")
    integration_metadata = {
        "generated_source": str(generated_source) if generated_source else "generated_in_current_run",
        "inserted_function": integration_result["function_name"],
        "goal": validated_plan.get("goal"),
        "scientific_steps": validated_plan.get("steps", []),
        "implementation_apps": pattern.get("apps", []) if pattern else [],
        "model": model_name,
        "generation_validation_checks": generation_checks,
        "integration_validation_checks": integration_result["validation_checks"],
        "hashes": integration_result["hashes"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds")
    }
    metadata.setdefault("integrations", []).append(integration_metadata)
    metadata["current_preview_sha256"] = integration_result["hashes"]["preview_sha256"]
    metadata["updated_at"] = integration_metadata["created_at"]
    metadata_temporary = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    workflow_temporary.write_text(integration_result["preview_code"], encoding="utf-8")
    diff_temporary.write_text(integration_result["diff"], encoding="utf-8")
    metadata_temporary.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    workflow_temporary.replace(workflow_path)
    diff_temporary.replace(diff_path)
    metadata_temporary.replace(metadata_path)
    return workflow_path, diff_path, metadata_path

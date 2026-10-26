import ast
import difflib
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .output import normalize_workflow_name


class ArtifactError(ValueError):
    pass


ARTIFACT_ROOTS = {
    "preview": Path("previews"),
    "generated": Path("generated_workflows")
}

TRASH_DIRECTORIES = {
    "preview": "previews",
    "generated": "generated"
}


def _artifact_name(raw_name):
    source_name = Path(raw_name).stem if Path(raw_name).suffix else raw_name
    return normalize_workflow_name(source_name)


def _write_json_atomic(file_path, content):
    temporary_path = file_path.with_suffix(file_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(content, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    temporary_path.replace(file_path)


def _write_text_atomic(file_path, content):
    temporary_path = file_path.with_suffix(file_path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(file_path)


def _sha256(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_python(source_code, file_path):
    try:
        ast.parse(source_code, filename=str(file_path))
    except SyntaxError as error:
        raise ArtifactError(
            f"Preview contains invalid Python syntax at line {error.lineno}: {error.msg}"
        ) from error


def _read_json(file_path, label):
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactError(f"Invalid {label}: {file_path}: {error}") from error


def _generated_paths(name):
    normalized_name = _artifact_name(name)
    root = ARTIFACT_ROOTS["generated"]
    return normalized_name, root / f"{normalized_name}.py", root / "metadata" / f"{normalized_name}.json"


def _preview_path(name):
    normalized_name = _artifact_name(name)
    return normalized_name, ARTIFACT_ROOTS["preview"] / normalized_name


def list_artifacts(artifact_type="all"):
    result = {"generated": [], "preview": []}
    if artifact_type in ("all", "generated"):
        generated_root = ARTIFACT_ROOTS["generated"]
        if generated_root.is_dir():
            result["generated"] = sorted(path.stem for path in generated_root.glob("*.py"))
    if artifact_type in ("all", "preview"):
        preview_root = ARTIFACT_ROOTS["preview"]
        if preview_root.is_dir():
            result["preview"] = sorted(
                path.name for path in preview_root.iterdir() if path.is_dir()
            )
    return result


def rename_artifact(artifact_type, old_name, new_name):
    old_name = _artifact_name(old_name)
    new_name = _artifact_name(new_name)
    if old_name == new_name:
        raise ArtifactError("The old and new artifact names are identical.")

    if artifact_type == "preview":
        _, old_path = _preview_path(old_name)
        _, new_path = _preview_path(new_name)
        if not old_path.is_dir():
            raise ArtifactError(f"Preview not found: {old_name}")
        if new_path.exists():
            raise ArtifactError(f"Preview already exists: {new_name}")
        metadata_path = new_path / "metadata.json"
        old_metadata_path = old_path / "metadata.json"
        metadata = _read_json(old_metadata_path, "preview metadata") if old_metadata_path.is_file() else None
        old_path.rename(new_path)
        try:
            if metadata is not None:
                metadata_path = new_path / "metadata.json"
                metadata["preview_name"] = new_name
                metadata["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                _write_json_atomic(metadata_path, metadata)
        except Exception:
            new_path.rename(old_path)
            raise
        return [new_path]

    old_name, old_code, old_metadata = _generated_paths(old_name)
    new_name, new_code, new_metadata = _generated_paths(new_name)
    if not old_code.is_file() or not old_metadata.is_file():
        raise ArtifactError(
            f"Generated artifact requires both code and metadata files: {old_name}"
        )
    if new_code.exists() or new_metadata.exists():
        raise ArtifactError(f"Generated artifact already exists: {new_name}")
    metadata = _read_json(old_metadata, "generated workflow metadata")
    old_code.rename(new_code)
    try:
        old_metadata.rename(new_metadata)
        metadata["artifact_name"] = new_name
        metadata["artifact_file"] = str(new_code)
        _write_json_atomic(new_metadata, metadata)
    except Exception:
        if new_metadata.exists():
            new_metadata.rename(old_metadata)
        if new_code.exists():
            new_code.rename(old_code)
        raise
    return [new_code, new_metadata]


def sync_preview(name):
    name, preview_path = _preview_path(name)
    metadata_path = preview_path / "metadata.json"
    diff_path = preview_path / "changes.diff"
    if not preview_path.is_dir() or not metadata_path.is_file():
        raise ArtifactError(f"Preview not found or incomplete: {name}")

    metadata = _read_json(metadata_path, "preview metadata")
    project_path = Path.cwd().resolve()
    resolved_preview = preview_path.resolve()
    source_path = (project_path / metadata.get("source_workflow", "")).resolve()
    workflow_filename = metadata.get("workflow_file") or source_path.name
    workflow_path = (resolved_preview / workflow_filename).resolve()
    try:
        resolved_preview.relative_to(project_path)
        source_path.relative_to(project_path)
        workflow_path.relative_to(resolved_preview)
    except ValueError as error:
        raise ArtifactError("Preview metadata contains a path outside the project.") from error
    if not source_path.is_file():
        raise ArtifactError(f"Original preview source not found: {source_path}")
    if not workflow_path.is_file():
        raise ArtifactError(f"Preview workflow file not found: {workflow_path}")

    source_code = source_path.read_text(encoding="utf-8")
    preview_code = workflow_path.read_text(encoding="utf-8")
    _validate_python(source_code, source_path)
    _validate_python(preview_code, workflow_path)

    source_hash = _sha256(source_code)
    expected_source_hash = metadata.get("source_sha256") or metadata.get(
        "hashes", {}
    ).get("original_sha256")
    if expected_source_hash and source_hash != expected_source_hash:
        raise ArtifactError(
            "The original workflow changed after this preview was created. "
            "Create a new preview from the current source."
        )

    current_hash = _sha256(preview_code)
    previous_hash = metadata.get("current_preview_sha256") or metadata.get(
        "hashes", {}
    ).get("preview_sha256")
    if previous_hash == current_hash:
        return {
            "name": name,
            "workflow": workflow_path,
            "metadata": metadata_path,
            "diff": diff_path,
            "changed": False
        }

    diff_text = "".join(difflib.unified_diff(
        source_code.splitlines(keepends=True),
        preview_code.splitlines(keepends=True),
        fromfile=str(source_path.relative_to(project_path)),
        tofile=str(workflow_path.relative_to(project_path))
    ))
    accepted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata["workflow_file"] = workflow_filename
    metadata["source_sha256"] = source_hash
    metadata["current_preview_sha256"] = current_hash
    metadata["updated_at"] = accepted_at
    metadata.setdefault("events", []).append({
        "event": "manual_changes_accepted",
        "previous_preview_sha256": previous_hash,
        "current_preview_sha256": current_hash,
        "accepted_at": accepted_at
    })

    _write_text_atomic(diff_path, diff_text)
    _write_json_atomic(metadata_path, metadata)
    return {
        "name": name,
        "workflow": workflow_path,
        "metadata": metadata_path,
        "diff": diff_path,
        "changed": True
    }


def _trash_bundle(artifact_type, name):
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    trash_root = Path("trash") / TRASH_DIRECTORIES[artifact_type]
    bundle = trash_root / f"{timestamp}_{name}"
    counter = 2
    while bundle.exists():
        bundle = trash_root / f"{timestamp}_{name}_{counter}"
        counter += 1
    return bundle


def remove_artifact(artifact_type, name):
    name = _artifact_name(name)
    bundle = _trash_bundle(artifact_type, name)
    payload = bundle / "payload"

    if artifact_type == "preview":
        _, source_path = _preview_path(name)
        if not source_path.is_dir():
            raise ArtifactError(f"Preview not found: {name}")
        bundle.mkdir(parents=True, exist_ok=False)
        payload.mkdir()
        shutil.move(str(source_path), str(payload / name))
    else:
        _, code_path, metadata_path = _generated_paths(name)
        if not code_path.is_file() or not metadata_path.is_file():
            raise ArtifactError(
                f"Generated artifact requires both code and metadata files: {name}"
            )
        bundle.mkdir(parents=True, exist_ok=False)
        (payload / "metadata").mkdir(parents=True)
        shutil.move(str(code_path), str(payload / code_path.name))
        shutil.move(str(metadata_path), str(payload / "metadata" / metadata_path.name))

    manifest = {
        "artifact_type": artifact_type,
        "artifact_name": name,
        "removed_at": datetime.now().astimezone().isoformat(timespec="seconds")
    }
    _write_json_atomic(bundle / "artifact.json", manifest)
    return bundle


def _find_removed_bundle(artifact_type, name):
    name = _artifact_name(name)
    trash_root = Path("trash") / TRASH_DIRECTORIES[artifact_type]
    matches = []
    if trash_root.is_dir():
        for manifest_path in trash_root.glob("*/artifact.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("artifact_name") == name:
                matches.append((manifest.get("removed_at", ""), manifest_path.parent))
    if not matches:
        raise ArtifactError(f"Removed {artifact_type} artifact not found: {name}")
    return name, sorted(matches, reverse=True)[0][1]


def restore_artifact(artifact_type, name):
    name, bundle = _find_removed_bundle(artifact_type, name)
    payload = bundle / "payload"

    if artifact_type == "preview":
        source_path = payload / name
        _, destination = _preview_path(name)
        if destination.exists():
            raise ArtifactError(f"Preview already exists: {name}")
        if not source_path.is_dir():
            raise ArtifactError(f"Removed preview payload is incomplete: {bundle}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination))
        restored_paths = [destination]
    else:
        source_code = payload / f"{name}.py"
        source_metadata = payload / "metadata" / f"{name}.json"
        _, destination_code, destination_metadata = _generated_paths(name)
        if destination_code.exists() or destination_metadata.exists():
            raise ArtifactError(f"Generated artifact already exists: {name}")
        if not source_code.is_file() or not source_metadata.is_file():
            raise ArtifactError(f"Removed generated payload is incomplete: {bundle}")
        destination_code.parent.mkdir(parents=True, exist_ok=True)
        destination_metadata.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_code), str(destination_code))
        shutil.move(str(source_metadata), str(destination_metadata))
        restored_paths = [destination_code, destination_metadata]

    shutil.rmtree(bundle)
    return restored_paths


def run_artifact_command(arguments):
    action = arguments.artifact_action
    artifact_type = getattr(arguments, "artifact_type", "all")
    if action == "list":
        artifacts = list_artifacts(artifact_type)
        if artifact_type in ("all", "generated"):
            print("Generated workflows:")
            for name in artifacts["generated"]:
                print(f"  {name}")
            if not artifacts["generated"]:
                print("  (none)")
        if artifact_type in ("all", "preview"):
            print("Previews:")
            for name in artifacts["preview"]:
                print(f"  {name}")
            if not artifacts["preview"]:
                print("  (none)")
        return

    if action == "rename":
        paths = rename_artifact(artifact_type, arguments.old_name, arguments.new_name)
        print(f"{artifact_type.capitalize()} artifact renamed successfully:")
        for path in paths:
            print(f"  {path}")
        return

    if action == "sync":
        result = sync_preview(arguments.name)
        if result["changed"]:
            print(f"Preview synchronized successfully: {result['name']}")
            print(f"  Workflow: {result['workflow']}")
            print(f"  Diff: {result['diff']}")
            print(f"  Metadata: {result['metadata']}")
        else:
            print(f"Preview is already synchronized: {result['name']}")
        return

    name = _artifact_name(arguments.name)
    if action == "remove":
        if not arguments.yes:
            confirmation = input(f"Type '{name}' to move this artifact to trash: ").strip()
            if confirmation != name:
                print("Artifact removal cancelled.")
                return
        bundle = remove_artifact(artifact_type, name)
        print(f"Artifact moved to trash: {bundle}")
        print("It can be restored with the artifacts restore command.")
        return

    paths = restore_artifact(artifact_type, name)
    print(f"{artifact_type.capitalize()} artifact restored successfully:")
    for path in paths:
        print(f"  {path}")

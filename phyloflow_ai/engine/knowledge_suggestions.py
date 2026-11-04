import ast
import copy
import hashlib
import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from ..agents.knowledge_suggester import analyze_knowledge_gaps
from .paths import DATA_ROOT
from .source_context import load_relevant_source_context, source_index_status
from .graph import producer_dependency_paths, simulate_tool_chain
from .discovery import discover_implemented_workflow
from .schema_validation import validate_schema


SUGGESTIONS_ROOT = DATA_ROOT / "suggestions"


def _missing_artifacts(message):
    match = re.search(r"(?:requires|consumes) (\[[^\]]*\])", message)
    if not match:
        return []
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _producer_index(knowledge):
    producers = {}
    for app_name, specification in knowledge.get("apps", {}).items():
        for artifact in specification.get("produces", []):
            producers.setdefault(artifact, []).append(app_name)
    owners = {}
    for tool_name, specification in knowledge.get("tools", {}).items():
        for app_name in specification.get("apps", []):
            owners.setdefault(app_name, []).append(tool_name)
    return producers, owners


def _build_diagnostics(validation_results, knowledge):
    producers, owners = _producer_index(knowledge)
    grouped = {}
    for result in validation_results:
        if result.get("section") != "Patterns" or result.get("status") != "FAILED":
            continue
        artifacts = _missing_artifacts(result.get("message", ""))
        key = (tuple(artifacts), result.get("message", ""))
        group = grouped.setdefault(key, {
            "diagnostic_id": f"gap-{len(grouped) + 1}",
            "missing_artifacts": artifacts,
            "reason": result.get("message", ""),
            "affected_patterns": [],
        })
        group["affected_patterns"].append(result.get("name", "").split(" -> "))
    diagnostics = []
    for group in grouped.values():
        candidates = []
        relevant_tools = set()
        failing_app_match = re.match(r"'([^']+)'", group["reason"])
        if failing_app_match:
            relevant_tools.update(owners.get(failing_app_match.group(1), []))
        preceding_match = re.search(r"preceding step '([^']+)'", group["reason"])
        if preceding_match and preceding_match.group(1) in knowledge.get("tools", {}):
            relevant_tools.add(preceding_match.group(1))
        for pattern in group["affected_patterns"]:
            if pattern and pattern[0] in knowledge.get("tools", {}):
                relevant_tools.add(pattern[0])
        for artifact in group["missing_artifacts"]:
            apps = producers.get(artifact, [])
            tools = sorted({tool for app in apps for tool in owners.get(app, [])})
            relevant_tools.update(tools)
            candidates.append({
                "artifact": artifact,
                "producer_apps_in_adapter": apps,
                "producer_tools_in_adapter": tools,
                "dependency_paths": producer_dependency_paths(artifact, knowledge),
            })
        group["candidate_producers"] = candidates
        group["relevant_tools"] = sorted(relevant_tools)
        diagnostics.append(group)
    return diagnostics


def _validate_ai_analyses(analyses, diagnostics, source_context):
    valid_ids = {item["diagnostic_id"] for item in diagnostics}
    diagnostic_by_id = {item["diagnostic_id"]: item for item in diagnostics}
    sources_by_tool = {
        tool["display_name"]: {
            source["id"]: source
            for source in tool.get("registered_sources", []) if source.get("id")
        }
        for tool in source_context
    }
    validated = {}
    for analysis in analyses:
        if not isinstance(analysis, dict) or analysis.get("diagnostic_id") not in valid_ids:
            continue
        confidence = analysis.get("confidence", "low")
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        diagnostic = diagnostic_by_id[analysis["diagnostic_id"]]
        producer_tools = {
            tool
            for candidate in diagnostic.get("candidate_producers", [])
            for tool in candidate.get("producer_tools_in_adapter", [])
        }
        evidence_sources = {
            source_id: metadata
            for tool in producer_tools
            for source_id, metadata in sources_by_tool.get(tool, {}).items()
        }
        accepted_source_ids = [
            source_id for source_id in analysis.get("source_ids", [])
            if source_id in evidence_sources
        ]
        if any(
            evidence_sources[source_id].get("origin", "").startswith("user")
            and evidence_sources[source_id].get("review_status") != "reviewed"
            for source_id in accepted_source_ids
        ) and confidence == "high":
            confidence = "medium"
        if not accepted_source_ids:
            confidence = "low" if confidence == "high" else confidence
        feasible_paths = [
            path
            for candidate in diagnostic.get("candidate_producers", [])
            for path in candidate.get("dependency_paths", [])
            if not path.get("unresolved_artifacts")
        ]
        semantic_status = "producer_path_verified" if feasible_paths else "producer_path_unresolved"
        deterministic = _deterministic_analysis(diagnostic)
        validated[analysis["diagnostic_id"]] = {
            "interpretation": (
                str(analysis.get("interpretation", ""))
                if accepted_source_ids else deterministic["interpretation"]
            ),
            "recommended_action": deterministic["recommended_action"],
            "confidence": confidence,
            "assumptions": [str(value) for value in analysis.get("assumptions", [])],
            "source_ids": accepted_source_ids,
            "semantic_validation": {
                "status": semantic_status,
                "verified_paths": feasible_paths[:3],
                "llm_interpretation": str(analysis.get("interpretation", "")),
                "llm_recommendation": str(analysis.get("recommended_action", "")),
            },
        }
    return validated


def _deterministic_analysis(diagnostic):
    candidates = diagnostic.get("candidate_producers", [])
    described = [
        (item["artifact"], item["producer_tools_in_adapter"], item["producer_apps_in_adapter"])
        for item in candidates
    ]
    if not described or not any(tools or apps for _, tools, apps in described):
        return {
            "interpretation": "No producer for the missing artifact is declared in the adapter knowledge base.",
            "recommended_action": "Use the selected scientific contracts to propose a new adapter app or artifact conversion, then review and implement it before changing the official knowledge base.",
            "confidence": "high",
            "assumptions": [],
            "source_ids": [],
        }
    producer_text = []
    for artifact, tools, apps in described:
        names = tools or apps
        producer_text.append(f"{artifact} is produced by {', '.join(names)}")
    affected_tools = {
        tool for pattern in diagnostic.get("affected_patterns", []) for tool in pattern
    }
    producer_tools = {tool for _, tools, _ in described for tool in tools}
    producers_in_pattern = producer_tools & affected_tools
    feasible_paths = [
        path for item in candidates for path in item.get("dependency_paths", [])
        if not path.get("unresolved_artifacts")
    ]
    if producers_in_pattern:
        action = (
            "Review the pattern dataflow: a producer exists in the affected pattern, so the "
            "dependency may require a branch or a connection to an earlier result rather than "
            "a new scientific tool."
        )
    elif feasible_paths:
        app_paths = [" -> ".join(path["apps"]) for path in feasible_paths[:3]]
        action = (
            "Review and connect a complete producer dependency path before the failing app: "
            + "; ".join(app_paths) + "."
        )
    else:
        action = (
            "Either add the declared producer before the failing app, declare the artifact as an "
            "external input for a deliberately partial pattern, or document and implement a valid "
            "conversion. Revalidate before promoting any JSON change."
        )
    return {
        "interpretation": "; ".join(producer_text) + ". The failing pattern does not make that artifact available at the required point.",
        "recommended_action": action,
        "confidence": "high",
        "assumptions": [],
        "source_ids": [],
        "semantic_validation": {
            "status": "producer_path_verified" if feasible_paths else "producer_path_unresolved",
            "verified_paths": feasible_paths[:3],
        },
    }


def generate_knowledge_suggestions(
    adapter_name,
    validation_results,
    knowledge,
    source_profile="combined",
):
    diagnostics = _build_diagnostics(validation_results, knowledge)
    if not diagnostics:
        return {
            "schema_version": 1,
            "adapter": adapter_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "policy": "Candidate suggestions only; the adapter knowledge base was not modified.",
            "selection": {
                "source_profile": source_profile,
                "tools": [],
                "keywords": [],
                "source_index": source_index_status(source_profile),
            },
            "llm_status": "not_needed",
            "llm_error": None,
            "diagnostics": [],
            "suggestions": [],
        }
    tool_names = sorted({tool for item in diagnostics for tool in item["relevant_tools"]})
    keywords = sorted({
        artifact for item in diagnostics for artifact in item["missing_artifacts"]
    })
    source_context = load_relevant_source_context(
        tool_names,
        keywords,
        source_profile=source_profile,
    )
    index_status = source_index_status(source_profile)
    selected_tools = [item["display_name"] for item in source_context]
    llm_status = "completed"
    llm_error = None
    try:
        analyses = analyze_knowledge_gaps(diagnostics, source_context)
        validated = _validate_ai_analyses(analyses, diagnostics, source_context)
    except Exception as error:
        validated = {}
        llm_status = "unavailable"
        llm_error = str(error)
    suggestions = []
    for diagnostic in diagnostics:
        suggestion = dict(diagnostic)
        suggestion["root_cause"] = diagnostic["reason"]
        suggestion["affected_pattern_count"] = len(diagnostic["affected_patterns"])
        suggestion["candidate_producer_names"] = sorted({
            name
            for candidate in diagnostic["candidate_producers"]
            for name in (
                candidate["producer_tools_in_adapter"]
                or candidate["producer_apps_in_adapter"]
            )
        })
        suggestion["status"] = "awaiting_human_review"
        deterministic = _deterministic_analysis(diagnostic)
        ai_analysis = validated.get(diagnostic["diagnostic_id"])
        suggestion["analysis"] = ai_analysis or deterministic
        suggestion["analysis_origin"] = "llm_schema_validated" if ai_analysis else "deterministic_fallback"
        suggestions.append(suggestion)
    return {
        "schema_version": 1,
        "adapter": adapter_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": "Candidate suggestions only; the adapter knowledge base was not modified.",
        "selection": {
            "source_profile": source_profile,
            "tools": selected_tools,
            "keywords": keywords,
            "source_index": index_status,
        },
        "llm_status": llm_status,
        "llm_error": llm_error,
        "diagnostics": diagnostics,
        "suggestions": suggestions,
    }


def save_knowledge_suggestions(report):
    validate_schema(report, "suggestion_report.schema.json")
    SUGGESTIONS_ROOT.mkdir(parents=True, exist_ok=True)
    path = SUGGESTIONS_ROOT / f"{report['adapter']}_adapter_suggestions.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _find_valid_prefix(pattern, knowledge, max_prefix=4):
    tools = list(knowledge.get("tools", {}))
    queue = deque([[]])
    while queue:
        prefix = queue.popleft()
        candidate = prefix + list(pattern)
        if simulate_tool_chain(candidate, knowledge, require_adjacent=True)[0] is None:
            return prefix
        if len(prefix) >= max_prefix:
            continue
        for tool in tools:
            if tool in prefix or tool in pattern:
                continue
            queue.append(prefix + [tool])
    return None


def _workflow_key(steps):
    value = "_".join(re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_") for step in steps)
    return f"candidate_{value}"


def _workflow_records(steps, knowledge_paths):
    records = []
    for path_value in knowledge_paths:
        path = Path(path_value)
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        for name, workflow in document.get("workflows", {}).items():
            if isinstance(workflow, dict):
                workflow_steps = workflow.get("steps", [])
                status = workflow.get("status", "implemented" if workflow.get("implemented") else "unknown")
                implemented = workflow.get("implemented", status == "implemented")
            elif isinstance(workflow, list):
                workflow_steps, status, implemented = workflow, "implemented", True
            else:
                continue
            if list(workflow_steps) == list(steps):
                records.append({
                    "source_type": "knowledge_json",
                    "source": str(path),
                    "workflow_name": name,
                    "status": status,
                    "implemented": bool(implemented),
                })
    return records


def _existing_implementation_records(steps, knowledge, knowledge_paths, workflow_source):
    records = _workflow_records(steps, knowledge_paths)
    if workflow_source:
        discovered = discover_implemented_workflow(steps, knowledge, workflow_file=workflow_source)
        if discovered:
            records.append({
                "source_type": "python_source",
                "source": str(discovered["source"]),
                "workflow_name": discovered["function_name"],
                "status": "implemented",
                "implemented": True,
                "apps": discovered["apps"],
            })
    return records


def build_candidate_knowledge_patch(
    report,
    knowledge,
    knowledge_path,
    reference_knowledge_paths=(),
    workflow_source=None,
):
    operations = []
    blocked = []
    duplicates = []
    seen_steps = set()
    simulated = copy.deepcopy(knowledge)
    simulated.setdefault("workflows", {})
    for suggestion in report.get("suggestions", []):
        handled_diagnostic = False
        for pattern in suggestion.get("affected_patterns", []):
            prefix = _find_valid_prefix(pattern, knowledge)
            if prefix is None:
                continue
            steps = prefix + pattern
            step_key = tuple(steps)
            if step_key in seen_steps:
                continue
            seen_steps.add(step_key)
            existing = _existing_implementation_records(
                steps,
                knowledge,
                [knowledge_path, *reference_knowledge_paths],
                workflow_source,
            )
            if existing:
                duplicates.append({
                    "diagnostic_id": suggestion["diagnostic_id"],
                    "steps": steps,
                    "reason": "Exact workflow already exists in knowledge or source code.",
                    "evidence": existing,
                })
                handled_diagnostic = True
                continue
            workflow_name = _workflow_key(steps)
            value = {
                "steps": steps,
                "status": "candidate",
                "implemented": False,
                "source": "adapter_validation_suggestion",
                "diagnostic_id": suggestion["diagnostic_id"],
                "review_status": "awaiting_human_review",
            }
            simulated["workflows"][workflow_name] = value
            error, _ = simulate_tool_chain(steps, simulated, require_adjacent=True)
            if error:
                continue
            operations.append({
                "op": "add",
                "path": f"/workflows/{workflow_name}",
                "value": value,
                "validation": "passed_artifact_dataflow_simulation",
            })
            handled_diagnostic = True
        if not handled_diagnostic:
            blocked.append({
                "diagnostic_id": suggestion["diagnostic_id"],
                "reason": "No JSON-only workflow addition passed artifact dataflow validation.",
                "required_follow_up": (
                    "Review the adapter implementation or define an evidenced conversion/app "
                    "before proposing a knowledge JSON operation."
                ),
            })
    target = Path(knowledge_path)
    base_bytes = target.read_bytes()
    return {
        "schema_version": 1,
        "adapter": report["adapter"],
        "target_file": str(target),
        "base_sha256": hashlib.sha256(base_bytes).hexdigest(),
        "policy": "Candidate JSON Patch-like operations only; not applied automatically.",
        "operations": operations,
        "duplicate_candidates": duplicates,
        "blocked_candidates": blocked,
        "review_status": "awaiting_human_review",
    }


def print_candidate_knowledge_patch(candidate):
    print("\nCandidate knowledge patch:")
    print(f"  Validated operations: {len(candidate['operations'])}")
    for operation in candidate["operations"]:
        steps = operation["value"]["steps"]
        print(f"  ADD {operation['path']}: {' -> '.join(steps)}")
    print(f"  Duplicate candidates: {len(candidate.get('duplicate_candidates', []))}")
    for duplicate in candidate.get("duplicate_candidates", []):
        print(f"  SKIP duplicate: {' -> '.join(duplicate['steps'])}")
        for evidence in duplicate["evidence"]:
            print(f"    Found in {evidence['source']}: {evidence['workflow_name']}")
    print(f"  Blocked candidates: {len(candidate['blocked_candidates'])}")
    for blocked in candidate["blocked_candidates"]:
        print(f"  {blocked['diagnostic_id']}: {blocked['reason']}")


def print_knowledge_suggestions(report):
    print("\nKnowledge suggestions:")
    if not report.get("suggestions"):
        print("  No failed patterns require suggestions.")
        return
    index_state = report.get("selection", {}).get("source_index", {})
    if index_state.get("status") in {"missing", "invalid", "stale"}:
        reasons = "; ".join(index_state.get("reasons", []))
        print(f"  Source index: {index_state.get('status', 'unknown').upper()} - {reasons}")
        print("  Run 'python phylo_agent2.py sources ingest' to refresh documentary evidence.")
    for index, suggestion in enumerate(report["suggestions"], 1):
        print(f"  {index}. Root cause: {suggestion['reason']}")
        print(f"     Affected patterns: {len(suggestion['affected_patterns'])}")
        for candidate in suggestion["candidate_producers"]:
            producers = candidate["producer_tools_in_adapter"] or candidate["producer_apps_in_adapter"]
            print(f"     Missing artifact: {candidate['artifact']}")
            print(f"     Candidate producers: {', '.join(producers) if producers else 'none found'}")
        analysis = suggestion["analysis"]
        print(f"     Analysis origin: {suggestion['analysis_origin']}")
        print(f"     Interpretation: {analysis['interpretation']}")
        print(f"     Recommended action: {analysis['recommended_action']}")
        print(f"     Confidence: {analysis['confidence'].upper()}")
        semantic = analysis.get("semantic_validation", {})
        if semantic:
            print(f"     Semantic validation: {semantic.get('status', 'unknown')}")
        if analysis["source_ids"]:
            print(f"     Sources: {', '.join(analysis['source_ids'])}")

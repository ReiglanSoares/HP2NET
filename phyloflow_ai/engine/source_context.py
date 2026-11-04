import json
import hashlib
import os
import re
from pathlib import Path


SOURCES_ROOT = Path(__file__).resolve().parents[1] / "sources"
REGISTRY_PATH = SOURCES_ROOT / "source_registry.json"
USER_SOURCES_ROOT = Path(os.getenv("PHYLOFLOW_SOURCES_DIR", "phyloflow_sources"))
USER_REGISTRY_PATH = USER_SOURCES_ROOT / "registry.json"
INDEX_ROOT = USER_SOURCES_ROOT / ".index"
LEGACY_INDEX_MANIFEST = INDEX_ROOT / "manifest.json"
SOURCE_PROFILES = {"official", "user_only", "combined"}


def profile_index_root(source_profile="combined"):
    if source_profile not in SOURCE_PROFILES:
        raise ValueError(f"Unknown source profile: {source_profile}")
    return INDEX_ROOT / source_profile


def profile_manifest_path(source_profile="combined", allow_legacy=True):
    path = profile_index_root(source_profile) / "manifest.json"
    if source_profile == "combined" and allow_legacy and not path.is_file():
        return LEGACY_INDEX_MANIFEST
    return path


def source_registry_fingerprint(source_profile="combined"):
    digest = hashlib.sha256()
    if source_profile == "official":
        paths = (REGISTRY_PATH,)
    elif source_profile == "user_only":
        paths = (USER_REGISTRY_PATH,)
    else:
        paths = (REGISTRY_PATH, USER_REGISTRY_PATH)
    for path in paths:
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def source_index_status(source_profile="combined"):
    manifest_path = profile_manifest_path(source_profile)
    if not manifest_path.is_file():
        return {
            "profile": source_profile,
            "status": "missing",
            "reasons": [f"Source profile '{source_profile}' has not been ingested."],
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {"profile": source_profile, "status": "invalid", "reasons": [str(error)]}
    reasons = []
    manifest_profile = manifest.get("source_profile") or (
        "user_only" if manifest.get("source_scope") == "user_only" else "combined"
    )
    if manifest_profile != source_profile:
        reasons.append(
            f"Manifest profile is '{manifest_profile}', expected '{source_profile}'."
        )
    if manifest.get("registry_sha256") != source_registry_fingerprint(source_profile):
        reasons.append("The source registry changed after the last ingestion.")
    for entry in manifest.get("entries", []):
        if entry.get("status") != "indexed":
            continue
        chunk_file = Path(entry.get("chunk_file", ""))
        if not chunk_file.is_file():
            reasons.append(f"Missing chunk file for {entry.get('source_id')}.")
            continue
        local_file = Path(entry.get("local_file", ""))
        if local_file.is_file():
            current = hashlib.sha256(local_file.read_bytes()).hexdigest()
            if current != entry.get("source_sha256"):
                reasons.append(f"Source {entry.get('source_id')} changed after ingestion.")
    return {
        "profile": source_profile,
        "status": "stale" if reasons else "current",
        "reasons": reasons,
    }


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _pdf_text(path):
    reader_class = None
    try:
        from pypdf import PdfReader
        reader_class = PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
            reader_class = PdfReader
        except ImportError:
            return None
    try:
        reader = reader_class(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return None


def _relevant_excerpt(text, keywords, limit=3500):
    if not text:
        return ""
    lowered = text.lower()
    positions = sorted({
        lowered.find(keyword.lower())
        for keyword in keywords
        if keyword and lowered.find(keyword.lower()) >= 0
    })
    if not positions:
        return text[:limit]
    chunks = []
    remaining = limit
    for position in positions[:6]:
        if remaining <= 0:
            break
        start = max(0, position - 350)
        chunk = text[start:start + min(900, remaining)]
        chunks.append(chunk)
        remaining -= len(chunk)
    return "\n...\n".join(chunks)


def _safe_source_path(root, local_path):
    root = root.resolve()
    path = (root / local_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _registry_tool_entries(source_profile="combined"):
    if source_profile is True:
        source_profile = "user_only"
    if source_profile in {"official", "combined"}:
        official = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        for tool_id, specification in official.get("tools", {}).items():
            yield SOURCES_ROOT, "official", tool_id, specification
    if source_profile == "official" or not USER_REGISTRY_PATH.is_file():
        return
    user = json.loads(USER_REGISTRY_PATH.read_text(encoding="utf-8"))
    for tool_id, specification in user.get("tools", {}).items():
        yield USER_SOURCES_ROOT, "user", tool_id, specification
    for source in user.get("shared_sources", []):
        for tool_id in source.get("tool_ids", []):
            yield USER_SOURCES_ROOT, "user_shared", tool_id, {
                "display_name": tool_id,
                "aliases": [],
                "sources": [source],
            }


def _indexed_excerpts(requested, keywords, source_profile="combined", limit_per_tool=3):
    manifest_path = profile_manifest_path(source_profile)
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates = {}
    terms = set()
    for value in keywords:
        if not value:
            continue
        terms.add(_normalize(value))
        terms.update(
            _normalize(part) for part in re.split(r"[_\W]+", str(value)) if len(part) >= 4
        )
    terms.discard("")
    for entry in manifest.get("entries", []):
        if entry.get("status") != "indexed" or _normalize(entry.get("tool_id")) not in requested:
            continue
        chunk_path = Path(entry.get("chunk_file", ""))
        if not chunk_path.is_file():
            continue
        payload = json.loads(chunk_path.read_text(encoding="utf-8"))
        for chunk in payload.get("chunks", []):
            normalized = _normalize(chunk.get("text", ""))
            score = sum(normalized.count(term) for term in terms)
            if score or not terms:
                excerpt = {
                "source_id": entry["source_id"],
                "origin": f"indexed:{entry.get('origin', 'unknown')}",
                "page": chunk.get("page"),
                "chunk_id": chunk.get("chunk_id"),
                "relevance_score": score,
                "excerpt": chunk.get("text", "")[:1100],
                }
                candidates.setdefault(_normalize(entry["tool_id"]), []).append((score, excerpt))
    results = {}
    for tool_id, ranked in candidates.items():
        ranked.sort(key=lambda item: item[0], reverse=True)
        results[tool_id] = [excerpt for _, excerpt in ranked[:limit_per_tool]]
    return results


def load_relevant_source_context(tool_names, keywords=(), source_profile="combined"):
    requested = {_normalize(name) for name in tool_names}
    indexed = _indexed_excerpts(requested, keywords, source_profile)
    indexed_source_ids = {
        excerpt["source_id"] for excerpts in indexed.values() for excerpt in excerpts
    }
    selected = {}
    for source_root, origin, tool_id, specification in _registry_tool_entries(source_profile):
        names = [tool_id, specification.get("display_name", "")]
        names.extend(specification.get("aliases", []))
        if not requested.intersection({_normalize(name) for name in names}):
            continue
        contract_location = specification.get("contract_path")
        if contract_location:
            contract_path = _safe_source_path(source_root, contract_location)
        else:
            manual_directory = specification.get("manual_directory")
            contract_path = (
                _safe_source_path(source_root, manual_directory)
                / "extracted_contract_candidate.json"
                if manual_directory and _safe_source_path(source_root, manual_directory)
                else None
            )
        contract = None
        if contract_path and contract_path.is_file():
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        excerpts = []
        search_terms = list(keywords) + names
        for source in specification.get("sources", []):
            local_path = source.get("local_path")
            if not local_path or source.get("id") in indexed_source_ids:
                continue
            path = _safe_source_path(source_root, local_path)
            if path is None or not path.is_file():
                continue
            text = None
            if path.suffix.lower() in {".md", ".txt", ".rst"}:
                text = path.read_text(encoding="utf-8", errors="replace")
            elif path.suffix.lower() == ".pdf":
                text = _pdf_text(path)
            excerpt = _relevant_excerpt(text, search_terms) if text else ""
            if excerpt:
                excerpts.append({"source_id": source["id"], "origin": origin, "excerpt": excerpt})
        key = _normalize(specification.get("display_name", tool_id))
        item = selected.setdefault(key, {
            "tool_id": tool_id,
            "display_name": specification.get("display_name", tool_id),
            "contract": None,
            "additional_contracts": [],
            "relevant_excerpts": [],
            "registered_sources": [],
        })
        if contract:
            if item["contract"] is None:
                item["contract"] = contract
            else:
                item["additional_contracts"].append({"origin": origin, "contract": contract})
        item["relevant_excerpts"].extend(excerpts)
        item["registered_sources"].extend([
            {"id": source.get("id"), "title": source.get("title"),
             "official_url": source.get("official_url"), "origin": origin,
             "review_status": source.get("review_status", "unreviewed")}
            for source in specification.get("sources", [])
        ])
    for item in selected.values():
        item["relevant_excerpts"].extend(indexed.get(_normalize(item["tool_id"]), []))
    return list(selected.values())

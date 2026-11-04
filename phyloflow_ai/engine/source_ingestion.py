import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests

from .source_context import (
    SOURCE_PROFILES,
    USER_SOURCES_ROOT,
    _registry_tool_entries,
    _safe_source_path,
    profile_index_root,
    profile_manifest_path,
    source_index_status,
    source_registry_fingerprint,
)
from .schema_validation import validate_schema


MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


class SourceIngestionError(RuntimeError):
    pass


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "nav", "svg", "noscript"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "nav", "svg", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data):
        if not self.ignored_depth and data.strip():
            self.parts.append(data.strip())

    def text(self):
        return "\n".join(self.parts)


def _slug(value):
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value)).strip("_") or "source"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url, source_id, cache_root, force=False):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SourceIngestionError("Only HTTP and HTTPS source URLs are allowed.")
    suffix = Path(parsed.path).suffix.lower()
    cached = cache_root / f"{_slug(source_id)}{suffix if suffix in {'.pdf', '.html', '.htm', '.md', '.txt'} else '.download'}"
    if cached.is_file() and not force:
        return cached
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_suffix(cached.suffix + ".part")
    size = 0
    try:
        with requests.get(url, stream=True, timeout=(10, 60), headers={"User-Agent": "PhyloFlowAI/1.0"}) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise SourceIngestionError("Remote source exceeds the 25 MiB download limit.")
            content_type = response.headers.get("Content-Type", "").lower()
            if suffix not in {".pdf", ".html", ".htm", ".md", ".txt"}:
                if "pdf" in content_type:
                    cached = cached.with_suffix(".pdf")
                elif "html" in content_type:
                    cached = cached.with_suffix(".html")
                temporary = cached.with_suffix(cached.suffix + ".part")
            with temporary.open("wb") as output:
                for block in response.iter_content(64 * 1024):
                    if not block:
                        continue
                    size += len(block)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise SourceIngestionError("Remote source exceeds the 25 MiB download limit.")
                    output.write(block)
        temporary.replace(cached)
        return cached
    finally:
        if temporary.exists():
            temporary.unlink()


def _pdf_pages(path):
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise SourceIngestionError("PDF extraction requires pypdf. Install project requirements first.") from error
    reader = PdfReader(str(path))
    return [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]


def _document_units(path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf_pages(path), "pdf"
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".html", ".htm", ".download"} and "<" in text:
        parser = _HTMLTextExtractor()
        parser.feed(text)
        return [(None, parser.text())], "html"
    return [(None, text)], "text"


def _chunks(units, chunk_size=1600, overlap=200):
    result = []
    for page, raw_text in units:
        text = re.sub(r"[ \t]+", " ", raw_text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            if end < len(text):
                boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
                if boundary > start + chunk_size // 2:
                    end = boundary + 1
            chunk = text[start:end].strip()
            if chunk:
                result.append({"page": page, "text": chunk})
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return result


def _registered_sources(tool_filter=None, source_profile="combined"):
    requested = tool_filter.lower() if tool_filter else None
    seen = set()
    for root, origin, tool_id, specification in _registry_tool_entries(source_profile):
        names = {tool_id.lower(), str(specification.get("display_name", "")).lower()}
        names.update(str(value).lower() for value in specification.get("aliases", []))
        if requested and requested not in names:
            continue
        for source in specification.get("sources", []):
            source_id = source.get("id")
            if not source_id:
                continue
            key = (origin, tool_id, source_id)
            if key in seen:
                continue
            seen.add(key)
            yield root, origin, tool_id, source


def ingest_sources(tool=None, allow_web=True, force=False, source_profile="combined"):
    if source_profile not in SOURCE_PROFILES:
        raise SourceIngestionError(f"Unknown source profile: {source_profile}")
    index_root = profile_index_root(source_profile)
    chunk_root = index_root / "chunks"
    cache_root = index_root / "web_cache"
    manifest_path = profile_manifest_path(source_profile, allow_legacy=False)
    chunk_root.mkdir(parents=True, exist_ok=True)
    entries = []
    for root, origin, tool_id, source in _registered_sources(tool, source_profile):
        entry = {
            "tool_id": tool_id,
            "source_id": source["id"],
            "origin": origin,
            "title": source.get("title"),
            "official_url": source.get("official_url"),
            "review_status": source.get("review_status", "unreviewed"),
        }
        try:
            path = None
            if source.get("local_path"):
                path = _safe_source_path(root, source["local_path"])
                if path is not None and not path.is_file():
                    path = None
            if path is None and source.get("official_url") and allow_web:
                path = _download(source["official_url"], source["id"], cache_root, force)
            elif path is None and source.get("official_url"):
                entry.update({"status": "skipped", "reason": "web access disabled"})
                entries.append(entry)
                continue
            elif path is None:
                raise SourceIngestionError("Source has neither a readable local_path nor official_url.")
            units, content_type = _document_units(path)
            chunks = _chunks(units)
            if not chunks:
                raise SourceIngestionError("No readable text was extracted from the source.")
            for index, chunk in enumerate(chunks):
                chunk["chunk_id"] = f"{source['id']}:{index}"
            chunk_path = chunk_root / f"{_slug(tool_id)}__{_slug(source['id'])}.json"
            payload = {
                "schema_version": 1,
                "tool_id": tool_id,
                "source_id": source["id"],
                "origin": origin,
                "content_type": content_type,
                "source_sha256": _sha256(path),
                "chunks": chunks,
            }
            chunk_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            entry.update({
                "status": "indexed",
                "local_file": str(path),
                "source_sha256": payload["source_sha256"],
                "chunk_file": str(chunk_path),
                "chunk_count": len(chunks),
            })
        except Exception as error:
            entry.update({"status": "failed", "error": str(error)})
        entries.append(entry)
    if manifest_path.is_file():
        previous_entries = json.loads(manifest_path.read_text(encoding="utf-8")).get("entries", [])
    else:
        previous_entries = []
    merged = {
        (entry.get("origin"), entry.get("tool_id"), entry.get("source_id")): entry
        for entry in previous_entries
    }
    for entry in entries:
        key = (entry.get("origin"), entry.get("tool_id"), entry.get("source_id"))
        previous = merged.get(key)
        if entry.get("status") == "skipped" and previous:
            continue
        merged[key] = entry
    manifest = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_profile": source_profile,
        "registry_sha256": source_registry_fingerprint(source_profile),
        "entries": sorted(
            merged.values(),
            key=lambda entry: (entry.get("tool_id", ""), entry.get("source_id", "")),
        ),
    }
    index_root.mkdir(parents=True, exist_ok=True)
    validate_schema(manifest, "source_manifest.schema.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    manifest["_processed_entries"] = entries
    return manifest


def validate_source_library(source_profile="combined"):
    results = []
    try:
        import pypdf  # noqa: F401
        results.append(("PDF extractor", "PASSED", "pypdf available"))
    except ImportError:
        results.append(("PDF extractor", "FAILED", "pypdf is not installed"))
    for root, origin, tool_id, source in _registered_sources(
        source_profile=source_profile
    ):
        status, message = "PASSED", "registered"
        if source.get("local_path"):
            path = _safe_source_path(root, source["local_path"])
            if path is None or not path.is_file():
                status, message = "FAILED", f"missing {source['local_path']}"
            elif source.get("sha256") and _sha256(path) != source["sha256"]:
                status, message = "FAILED", f"SHA-256 mismatch for {source['local_path']}"
        elif not source.get("official_url"):
            status, message = "FAILED", "missing local_path and official_url"
        results.append((f"{tool_id}/{source['id']}", status, f"{origin}: {message}"))
    manifest_path = profile_manifest_path(source_profile)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("entries", []):
            if entry.get("status") == "failed":
                results.append((
                    f"index/{entry['tool_id']}/{entry['source_id']}",
                    "WARNING",
                    entry.get("error", "indexing failed"),
                ))
        freshness = source_index_status(source_profile)
        results.append((
            "Source index freshness",
            "WARNING" if freshness["status"] == "stale" else "PASSED",
            "; ".join(freshness["reasons"]) if freshness["reasons"] else "current",
        ))
    return results


def list_indexed_sources(source_profile="combined"):
    manifest_path = profile_manifest_path(source_profile)
    if not manifest_path.is_file():
        return []
    return json.loads(manifest_path.read_text(encoding="utf-8")).get("entries", [])


def run_source_command(arguments):
    if arguments.source_action == "ingest":
        source_profile = "user_only" if arguments.user_only else arguments.profile
        manifest = ingest_sources(
            arguments.tool,
            not arguments.no_web,
            arguments.force,
            source_profile,
        )
        processed = manifest.get("_processed_entries", manifest["entries"])
        indexed = sum(entry["status"] == "indexed" for entry in processed)
        failed = sum(entry["status"] == "failed" for entry in processed)
        skipped = sum(entry["status"] == "skipped" for entry in processed)
        print(f"Source ingestion completed: {indexed} indexed, {skipped} skipped, {failed} failed.")
        print(f"Source profile: {manifest['source_profile']}")
        print(f"Manifest: {profile_manifest_path(source_profile, allow_legacy=False)}")
        for entry in processed:
            if entry["status"] == "failed":
                print(f"  FAILED {entry['tool_id']}/{entry['source_id']}: {entry['error']}")
        return failed == 0
    if arguments.source_action == "validate":
        results = validate_source_library(arguments.profile)
        for name, status, message in results:
            print(f"{name}: {status} - {message}")
        return all(status != "FAILED" for _, status, _ in results)
    entries = list_indexed_sources(arguments.profile)
    if not entries:
        if profile_manifest_path(arguments.profile).is_file():
            print(f"Source profile '{arguments.profile}' is indexed but contains no sources.")
        else:
            print(
                f"No '{arguments.profile}' source index found. "
                f"Run 'sources ingest --profile {arguments.profile}' first."
            )
        return True
    for entry in entries:
        suffix = (
            f" ({entry.get('chunk_count', 0)} chunks)"
            if entry["status"] == "indexed"
            else f" - {entry.get('error') or entry.get('reason', '')}"
        )
        print(f"{entry['tool_id']}/{entry['source_id']}: {entry['status'].upper()}{suffix}")
    return True

# PhyloFlowAI scientific sources

This directory stores project-independent documentation about phylogenetic tools.
Adapters may reference these sources, but the documents describe the tools themselves
and are not owned by a specific integration such as HP2NET.

Place manuals, official papers, release notes, and other authoritative local documents
inside the corresponding directory under `manuals/`. Register every source in
`source_registry.json`, including its tool version and provenance when known.

The registry initially marks tools as `awaiting_sources`. Adding a file does not make
its extracted claims official automatically: AI-generated contracts remain candidates
until deterministic validation and human review are complete.

Each item added to a tool's `sources` list should follow this shape:

```json
{
  "id": "stable_unique_id",
  "type": "official_manual",
  "title": "Document title",
  "version": "documented tool version",
  "local_path": "manuals/tool/document.pdf",
  "official_url": null,
  "retrieved_at": null,
  "sha256": null,
  "review_status": "unreviewed"
}
```


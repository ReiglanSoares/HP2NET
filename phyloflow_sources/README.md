# PhyloFlowAI user document library

This directory stores documents supplied by the user. It is separate from
`phyloflow_ai/sources/` (sources distributed with the application) and from
`phyloflow_workspace/` (generated runtime data).

- Put tool-specific documents under `tools/<tool_id>/`.
- Put documents covering multiple tools under `shared/`.
- Register readable documents in `registry.json`.

Example tool entry:

```json
{
  "tools": {
    "raxml": {
      "display_name": "RAxML",
      "aliases": ["standard-RAxML"],
      "contract_path": null,
      "sources": [
        {
          "id": "user_raxml_notes",
          "type": "user_document",
          "title": "Local RAxML notes",
          "local_path": "tools/raxml/notes.md",
          "official_url": null,
          "review_status": "unreviewed"
        }
      ]
    }
  }
}
```

Set `PHYLOFLOW_SOURCES_DIR` to place this library elsewhere, including another disk.
User sources can support candidate suggestions but are never promoted automatically.

Shared source entries should declare `"tool_ids": ["raxml", "astral"]` so relevance
selection can load them only for the tools involved in a diagnostic.

Adapter execution rules do not belong in this library. Keep them in the corresponding
package under `phyloflow_ai/adapters/`.

After registering documents, build the searchable index with:

```bash
python phylo_agent2.py sources ingest --profile user_only
```

The command reads local PDF, Markdown, TXT, RST, and HTML files. It also downloads
remote-only HTTP/HTTPS sources when web access is enabled. Downloads, extracted text
chunks, hashes, PDF page numbers, and the manifest are stored in the rebuildable
`.index/<profile>/` directory.

Available profiles are:

- `official`: application-maintained official sources only;
- `user_only`: documents registered in this user library only;
- `combined`: official and user sources together.

Build a fully local user-only index with:

```bash
python phylo_agent2.py sources ingest --profile user_only --no-web
```

List or validate a specific profile:

```bash
python phylo_agent2.py sources list --profile user_only
python phylo_agent2.py sources validate --profile user_only
```

When generating adapter suggestions, select an already-ingested profile with
`--source-profile`:

```bash
python phylo_agent2.py adapter validate hp2net --suggestions --source-profile user_only
```

The older `--user-only` ingestion option remains an alias for
`--profile user_only`.

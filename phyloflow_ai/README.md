# PhyloFlowAI

PhyloFlowAI is an independent tool for planning, validating, generating, and
previewing phylogenetic workflow extensions. This repository currently uses the
HP2NET adapter to exercise the reusable engine.

## Architecture

```text
phyloflow_ai/
├── agents/              Planner, code generator, and knowledge suggester
├── engine/              Graph, validation, discovery, dry run, previews, and assets
├── llm/                 Ollama client, streaming, metrics, and cancellation
├── schemas/             Versioned JSON contracts
├── sources/             Shared scientific contracts and official source registry
└── adapters/hp2net/     HP2NET-specific settings, patterns, and knowledge

phyloflow_sources/       Persistent user document library and search index
phyloflow_workspace/     Generated code, previews, suggestions, and trash
phylo_agent2.py          Backward-compatible HP2NET launcher
```

Adapter rules belong under `adapters/`. General scientific documentation belongs
under `sources/`. User PDFs, articles, and notes belong in `phyloflow_sources/`.

## Requirements and model

Install the project requirements and start [Ollama](https://ollama.com/). The current
default model is `llama3.1:8b` and can be changed in the tool configuration.

```bash
pip install -r requirements.txt
ollama pull llama3.1:8b
```

## Running

```bash
python3 phylo_agent2.py
python3 phylo_agent2.py --help
```

The package can also be started directly:

```bash
python3 -m phyloflow_ai --adapter hp2net --project .
```

Use `--print-code` to print validated code without saving it:

```bash
python3 phylo_agent2.py --print-code
```

Otherwise, generated code and metadata are stored in
`phyloflow_workspace/generated/`.

## Adapter validation and suggestions

Validate the HP2NET project, contracts, apps, artifacts, patterns, and configuration
without calling the LLM:

```bash
python3 phylo_agent2.py adapter validate hp2net
```

Generate evidence-aware explanations and a candidate knowledge patch:

```bash
python3 phylo_agent2.py adapter validate hp2net --suggestions
```

The complete report is saved in
`phyloflow_workspace/suggestions/hp2net_adapter_suggestions.json`. It records valid
operations, duplicates, blocked candidates, evidence, confidence, and original LLM
text for audit. Official adapter knowledge is never modified automatically.

For HP2NET suggestions, `adapters/hp2net/knowledge/code_discovery_knowledge.json` is
the only knowledge JSON used to diagnose gaps and compare candidate workflows. The
real `parsl_workflow.py` source is still inspected to avoid proposing code that is
already implemented. Legacy and test knowledge JSON files do not affect suggestions.

Dataflow validation can reuse compatible artifacts from earlier steps. Automatic
planning and patch generation remain linear and reject flows requiring an explicit
branch or merge that the generated function cannot represent.

`llm_schema_validated` means the LLM returned the expected JSON structure; it does
not mean its scientific claim was approved. Producer dependencies are checked
deterministically, and high confidence requires accepted documentary evidence.

## Scientific source ingestion

Index registered PDFs, text files, and web pages explicitly:

```bash
python3 phylo_agent2.py sources ingest
python3 phylo_agent2.py sources ingest --tool raxml
python3 phylo_agent2.py sources ingest --no-web
python3 phylo_agent2.py sources ingest --force
python3 phylo_agent2.py sources ingest --profile official
python3 phylo_agent2.py sources ingest --profile user_only
python3 phylo_agent2.py sources ingest --profile combined
python3 phylo_agent2.py sources list
python3 phylo_agent2.py sources validate
```

The rebuildable index is stored under `phyloflow_sources/.index/`. Suggestions receive
only relevant chunks, with source IDs and PDF pages when available. If a registry,
local source, or chunk changes, the index is marked stale and should be rebuilt.

Each profile has an independent manifest, chunk directory, and web cache under
`phyloflow_sources/.index/<profile>/`:

- `official`: only package-maintained official sources;
- `user_only`: only `tools/` and `shared/` entries from the user registry;
- `combined`: official and user sources together.

`--user-only` remains an alias for `--profile user_only`. Combine the user profile
with `--no-web` to guarantee that only local user files are read:

```bash
python3 phylo_agent2.py sources ingest --profile user_only --no-web
```

Choose the already-built profile when requesting suggestions:

```bash
python3 phylo_agent2.py adapter validate hp2net --suggestions --source-profile official
python3 phylo_agent2.py adapter validate hp2net --suggestions --source-profile user_only
python3 phylo_agent2.py adapter validate hp2net --suggestions --source-profile combined
```

## Integration previews

Create a safe workflow copy:

```bash
python3 phylo_agent2.py --new-preview experiment_1
```

Add new validated functions to the same cumulative preview:

```bash
python3 phylo_agent2.py --preview experiment_1
```

Create a preview from another workflow source or integrate existing generated code:

```bash
python3 phylo_agent2.py --new-preview experiment_2 --preview-source workflows/parsl_workflow_2.py
python3 phylo_agent2.py --preview experiment_1 --integrate-generated phyloflow_workspace/generated/raxml_astral.py
```

Previews keep their workflow copy, metadata history, hashes, and diff under
`phyloflow_workspace/previews/<name>/`.

## Asset management

These commands do not call the LLM:

```bash
python3 phylo_agent2.py assets list
python3 phylo_agent2.py assets list preview
python3 phylo_agent2.py assets list generated
python3 phylo_agent2.py assets rename preview experiment_1 final_experiment
python3 phylo_agent2.py assets rename generated raxml_astral raxml_astral_test
python3 phylo_agent2.py assets sync preview experiment_1
python3 phylo_agent2.py assets remove preview final_experiment
python3 phylo_agent2.py assets restore preview final_experiment
```

Removed assets remain recoverable under `phyloflow_workspace/trash/`.

## JSON schemas

The versioned schemas under `phyloflow_ai/schemas/` define suggestion reports,
candidate patches, and source manifests. They prevent malformed or incomplete JSON
from being saved. They validate structure, not scientific correctness.

## Storage configuration

- `PHYLOFLOW_WORKSPACE`: generated workspace location.
- `PHYLOFLOW_SOURCES_DIR`: persistent user document library location.
- `PHYLOFLOW_AI_DRY_RUN_TIMEOUT`: isolated dry-run timeout; default is 10 seconds.
- `PHYLOFLOW_AI_DATA_DIR`: older workspace variable retained for compatibility.

## Current limitations

- Isolated structural and dry-run validation does not replace execution in the real
  HP2NET, Parsl, or HPC environment.
- Automatic code generation currently represents linear compositions; explicit branch
  and merge generation remains future work.
- Adapter contracts and implementation patterns still require human review.
- Candidate knowledge changes are not applied automatically.
- OCR and embedding-based semantic search are not currently implemented.

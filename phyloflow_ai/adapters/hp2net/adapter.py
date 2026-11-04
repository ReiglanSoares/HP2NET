from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HP2NetAdapter:
    """Files and conventions used to connect the generic tool to HP2NET."""

    name: str = "hp2net"
    workflow_source: str = "parsl_workflow.py"
    apps_source: str = "apps.py"
    config_source: str = "bioconfig.py"
    data_directory: str = "phyloflow_workspace"
    generated_directory: str = "phyloflow_workspace/generated"
    preview_directory: str = "phyloflow_workspace/previews"
    trash_directory: str = "phyloflow_workspace/trash"

    @property
    def knowledge_directory(self):
        return Path(__file__).resolve().parent / "knowledge"

    @property
    def original_knowledge(self):
        return self.knowledge_directory / "hp2net_knowledge.json"

    @property
    def target_knowledge(self):
        return self.knowledge_directory / "code_discovery_knowledge.json"


ADAPTER = HP2NetAdapter()


"""Backward-compatible HP2NET entry point for PhyloFlowAI."""

from phyloflow_ai.cli import run


if __name__ == "__main__":
    raise SystemExit(run())

"""Workspace-level importability — every stub must import (SFP-23 acceptance)."""
import importlib

PACKAGES = [
    "external_events", "identity", "communication", "orchestrator", "workspace_worker",
    "sfp_contracts", "sfp_messaging", "sfp_observability", "sfp_testing",
    "sfp_agent_runtime", "sfp_config",
]

def test_all_packages_importable():
    for name in PACKAGES:
        mod = importlib.import_module(name)
        assert getattr(mod, "__version__", None) == "0.1.0", f"{name} missing __version__"

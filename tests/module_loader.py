"""Load integration modules without importing Home Assistant."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

INTEGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "victronusb"
)


def load_integration_module(module_name: str) -> ModuleType:
    """Import a module while replacing the integration package with a stub."""
    package_name = "custom_components.victronusb"
    package = sys.modules.get(package_name)
    if package is None:
        package = ModuleType(package_name)
        package.__path__ = [str(INTEGRATION_PATH)]
        sys.modules[package_name] = package

    full_name = f"{package_name}.{module_name}"
    sys.modules.pop(full_name, None)
    return importlib.import_module(full_name)

"""Lazy import compatibility for versioned Clay modules moved to ``_retired``.

The production package installs only this finder. Archived implementations are
not imported unless a caller explicitly requests an old versioned module name.
When an unversioned canonical promotion exists, the old name aliases that exact
module object so classes and registries keep a single identity.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional


_RETIRED_ROOT = Path(__file__).resolve().parent / "_retired"
_VERSIONED_MODULE_RE = re.compile(r"_v[0-9]+$")
_SEMANTIC_PROMOTIONS = {
    "clay.templates.dacfc_gbw_integer_scaling_v200": (
        "clay.templates.dacfc_gbw_integer_scaling"
    ),
    "clay.templates.dacfc_gbw_integer_scaling_v201": (
        "clay.templates.dacfc_gbw_continuous_scaling"
    ),
}


def _module_exists(root: Path, relative_name: str) -> bool:
    base = root.joinpath(*relative_name.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _retired_target_exists(relative_name: str) -> bool:
    return _module_exists(_RETIRED_ROOT, relative_name)


def _canonical_promotion(fullname: str) -> Optional[str]:
    semantic = _SEMANTIC_PROMOTIONS.get(fullname)
    if semantic is not None:
        return semantic
    leaf = fullname.rsplit(".", 1)[-1]
    if not _VERSIONED_MODULE_RE.search(leaf):
        return None
    candidate = _VERSIONED_MODULE_RE.sub("", fullname)
    if importlib.util.find_spec(candidate) is not None:
        return candidate
    return None


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str) -> None:
        self.target = target
        self._identity: Optional[tuple[object, object, object, str]] = None

    def create_module(self, spec: object) -> ModuleType:
        module = importlib.import_module(self.target)
        self._identity = (
            module.__spec__,
            module.__package__,
            module.__loader__,
            module.__name__,
        )
        return module

    def exec_module(self, module: ModuleType) -> None:
        # Import machinery temporarily overwrites these attributes with the alias
        # spec. Restore the canonical identity so one module/class object remains.
        if self._identity is not None:
            module.__spec__, module.__package__, module.__loader__, module.__name__ = (
                self._identity
            )


class _RetiredAliasFinder(importlib.abc.MetaPathFinder):
    _marker = "clay-retired-alias-finder-v2"

    def find_spec(
        self,
        fullname: str,
        path: Optional[list[str]] = None,
        target: Optional[ModuleType] = None,
    ):
        alias_target: Optional[str] = None
        if fullname.startswith("clay._retired."):
            relative = fullname.removeprefix("clay._retired.")
            if _retired_target_exists(relative):
                return None
            alias_target = f"clay.{relative}"
        elif fullname.startswith("clay."):
            relative = fullname.removeprefix("clay.")
            if not _VERSIONED_MODULE_RE.search(relative.rsplit(".", 1)[-1]):
                return None
            alias_target = _canonical_promotion(fullname)
            if alias_target is None and _retired_target_exists(relative):
                alias_target = f"clay._retired.{relative}"

        if alias_target is None:
            return None
        target_spec = importlib.util.find_spec(alias_target)
        if target_spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            _AliasLoader(alias_target),
            origin=target_spec.origin,
            is_package=target_spec.submodule_search_locations is not None,
        )


def install_retired_alias_finder() -> None:
    if not any(
        getattr(finder, "_marker", None) == _RetiredAliasFinder._marker
        for finder in sys.meta_path
    ):
        sys.meta_path.insert(0, _RetiredAliasFinder())

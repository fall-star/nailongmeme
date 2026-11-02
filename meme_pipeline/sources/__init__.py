from __future__ import annotations

import importlib
import pkgutil

from .base import SourceAdapter

_REGISTRY: dict[str, type[SourceAdapter]] = {}
_DISCOVERED = False


def register_source(adapter_class: type[SourceAdapter]) -> type[SourceAdapter]:
    key = adapter_class.key
    if not key or not isinstance(key, str):
        raise ValueError(f"{adapter_class!r} has no valid source key")
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not adapter_class:
        raise ValueError(f"source key {key!r} is already registered by {existing!r}")
    _REGISTRY[key] = adapter_class
    return adapter_class


def discover_sources() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True
    for module_info in pkgutil.iter_modules(__path__):
        name = module_info.name
        if name in {"base"} or name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{name}")
        adapter = getattr(module, "ADAPTER", None)
        if isinstance(adapter, SourceAdapter):
            register_source(type(adapter))


def get_source(key: str) -> type[SourceAdapter]:
    discover_sources()
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown source {key!r}; available: {available}") from exc


def list_sources() -> list[type[SourceAdapter]]:
    discover_sources()
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]

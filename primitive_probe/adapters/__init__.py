"""Adapter modules. Every *.py here is imported automatically and statically audited.

To probe a NEW primitive: add a module (or a function in an existing one) with
@register(..., targets=(the_production_function, ...)), then add a case JSON in
primitive_probe/cases/. See primitive_probe/README_zh.md.
"""
import importlib
import pkgutil


def load_all():
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f'{__name__}.{info.name}')

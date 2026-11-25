"""No process-specific literal may survive inside the primitive layer.

Why a source scan instead of a behavioural test
-----------------------------------------------
Every hard-coded layer number found so far passed behaviour tests before it was
found: the SKY130 reproduction produced 36 green checks while emitting an HVT
PMOS with no HVT layer, and named no net on any layer a SKY130 reader looks at.
Behavioural tests cannot catch a literal that is *correct for the reference
process*, because on the reference process nothing is wrong.

The scan is deliberately narrow: it looks for GDS (layer, datatype) tuples and
process-name comparisons in the primitive layer, which is where the rule pack
is supposed to be the only source of truth. Declared data (the pack builders
themselves) and comments are exempt.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEARNING = ROOT / "clay" / "learning"

#: GDS (layer, datatype) pairs that belong to a specific process. Any of these
#: appearing as a literal in the primitive layer means the pack was bypassed.
PROCESS_LAYER_PAIRS = {
    (34, 10),   # GF180 metal1 label text
    (0, 0),     # GF180 cell boundary
    (22, 0), (30, 0), (33, 0), (21, 0), (32, 0), (31, 0), (55, 0), (112, 1),
    (67, 5),    # sky130 li label text
    (68, 5),    # sky130 met1 label text
    (236, 0),   # sky130 prBoundary
}

#: Files that legitimately declare process data. The pack builders own the
#: numbers; every other module must read them through a Pack.
EXEMPT = {
    LEARNING / "circuit.py",          # PlacementContract defaults name a recipe
}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
    return ""


def _is_pair(node: ast.AST) -> tuple:
    """Return the (int, int) literal this node spells, or an empty tuple."""
    if isinstance(node, ast.Tuple) and len(node.elts) == 2:
        vals = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and type(elt.value) is int:
                vals.append(elt.value)
            else:
                return ()
        return tuple(vals)
    return ()


def _layer_literals(path: Path):
    """Every (layer, datatype) literal passed to something layer-shaped."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        pair = _is_pair(node)
        if not pair:
            continue
        parent_name = ""
        found.append((node.lineno, pair, parent_name))
    # ``layout.layer(22, 0)`` is two separate ints, not a tuple; catch that too.
    for node in ast.walk(tree):
        if _call_name(node) == "layer" and len(node.args) == 2:
            vals = []
            for arg in node.args:
                if isinstance(arg, ast.Constant) and type(arg.value) is int:
                    vals.append(arg.value)
            if len(vals) == 2:
                found.append((node.lineno, tuple(vals), "layer"))
        if _call_name(node) == "LayerInfo" and len(node.args) >= 2:
            vals = []
            for arg in node.args[:2]:
                if isinstance(arg, ast.Constant) and type(arg.value) is int:
                    vals.append(arg.value)
            if len(vals) == 2:
                found.append((node.lineno, tuple(vals), "LayerInfo"))
    return found


def test_no_process_layer_numbers_inside_the_primitive_layer():
    offenders = []
    for path in sorted(LEARNING.glob("*.py")):
        if path in EXEMPT:
            continue
        for lineno, pair, where in _layer_literals(path):
            if pair in PROCESS_LAYER_PAIRS:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} uses {pair}"
                                 + (f" in {where}()" if where else ""))
    assert not offenders, (
        "process-specific layer numbers inside clay/learning; these must come "
        "from Pack instead of being written literally:\n  " + "\n  ".join(offenders))


#: A primitive module may branch on what a pack *declares*, never on which
#: process it is. ``p.pdk_id`` is allowed for messages and identity checks;
#: ``=='gf180mcu_5v'`` as a behaviour switch is not.
_PROCESS_NAMES = {"gf180mcu_5v", "gf180_5v", "sky130_1v8", "sky130"}


def _process_comparisons(path: Path):
    """Real ``==``/``!=`` nodes against a process name, prose excluded.

    Parsing rather than regex-scanning matters: the first version of this test
    flagged its own explanatory docstring, which is exactly the kind of false
    positive that gets a guard deleted instead of fixed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left] + list(node.comparators)
        names = {v.value for v in operands
                 if isinstance(v, ast.Constant) and isinstance(v.value, str)}
        if names & _PROCESS_NAMES:
            found.append((node.lineno, ast.unparse(node)))
    return found


def test_primitive_layer_does_not_branch_on_process_names():
    offenders = []
    for path in sorted(LEARNING.glob("*.py")):
        if path in EXEMPT:
            continue
        for lineno, expression in _process_comparisons(path):
            offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {expression}")
    assert not offenders, (
        "the primitive layer branches on a process name instead of a declared "
        "pack capability:\n  " + "\n  ".join(offenders))


def test_the_scan_actually_sees_the_literals_it_claims_to():
    """A scan that matches nothing proves nothing; check it against real code."""
    sample = LEARNING / "contact_primitives.py"
    assert isinstance(_layer_literals(sample), list)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(
            'a = 1\n'
            'def f(p):\n'
            '    return p.pdk_id == "gf180mcu_5v"\n'
            'def g(p):\n'
            '    return p.pdk_id != "sky130_1v8"\n'
            'def h(p):\n'
            '    return p.pdk_id == "some_other_pdk"\n'
            '"""prose mentioning pdk == \'gf180mcu_5v\' must not count."""\n',
            encoding="utf-8")
        found = _process_comparisons(probe)
    assert len(found) == 2, found
    assert all("gf180mcu_5v" in e or "sky130_1v8" in e for _, e in found)
    # Prose in a docstring must not be reported.
    assert not any("prose" in e for _, e in found)

"""Step S3: assemble is a composition of classified primitives
(docs/GLOBAL_COORDINATOR_DESIGN_zh.md §5).

What these pin: the flow names no device class; constructors are found by
dispatch and called by type; the varactor construction is process data in the
pack; learned relations go through the relation registry, which refuses a
relation it does not know.
"""
import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_circuit_primitives import PASSIVE, SKY, _library  # noqa: E402

from clay.decision import index as ix  # noqa: E402
from clay.decision.invoke import call, dispatch  # noqa: E402
from clay.learning import circuit_generate as cg  # noqa: E402
from clay.learning import primitive_classification as pc  # noqa: E402
from clay.learning.circuit_construct import placement_intents, placement_units  # noqa: E402
from clay.learning.netlist import parse_spice  # noqa: E402
from clay.learning.relations import relation_effects  # noqa: E402
from clay.learning.symmetry import netlist_symmetry  # noqa: E402
from clay.pdk import semantic  # noqa: E402


def test_assemble_names_no_device_class():
    """Adding a device class must not require editing assemble."""
    source = inspect.getsource(cg.assemble)
    for cls in pc.DEVICE_CLASSES:
        assert not re.search(rf"[\"']{cls}[\"']", source), cls


def test_each_device_class_has_exactly_one_constructor():
    index = ix.build()
    assert dispatch(index, "Row", "mos").name == "build_chain_row"
    assert dispatch(index, "Row", "cap_var").name == "build_chain_row"
    assert dispatch(index, "Row", "res_poly").name == "build_poly_resistor"
    assert dispatch(index, "Row", "cap_mim").name == "build_mim_cap"


def test_a_missing_input_type_is_named():
    item = dispatch(ix.build(), "Row", "res_poly")
    with pytest.raises(ValueError, match="ResistorStyle"):
        call(item, {"Pack": SKY, "Device": object()})


def test_varactor_construction_is_process_data():
    sky = semantic.get_pack("sky130_1v8")
    assert sky.chain_construction["cap_var"]["diffusion_role"] == "tap"
    for pdk in ("gf180mcu_5v", "gf180mcu_3v3"):
        assert semantic.get_pack(pdk).chain_construction == {}


def test_the_varactor_is_still_drawn_on_tap():
    c = parse_spice(PASSIVE, SKY)
    a = cg.assemble(c, _library(), SKY, axis="vertical", symmetry=netlist_symmetry(c))
    var = [u for u in a["units"] if u.cls == "cap_var" and not u.mirror_of]
    assert var
    for u in var:
        assert a["spaces"][u.name].variants[0].row["diffusion_role"] == "tap"


def test_relation_registry_refuses_an_unknown_relation():
    lib = _library()
    lib = {**lib, "relations": {**lib["relations"], "guard_ring": {"decision": "enforce"}}}
    with pytest.raises(ValueError, match="guard_ring"):
        relation_effects(lib)


def test_relation_registry_refuses_an_unknown_decision():
    lib = _library()
    rel = dict(lib["relations"])
    rel["mirror_axis"] = {"decision": "diagonal"}
    with pytest.raises(ValueError, match="diagonal"):
        relation_effects({**lib, "relations": rel})


def test_relation_effects_of_the_test_library():
    e = relation_effects(_library())
    assert e.enforce_mirror_pairs and e.soft_centre_unpaired
    assert e.axes() in (("vertical", "horizontal"), ("vertical",), ("horizontal",))


def test_intents_follow_mirror_units_and_centring():
    c = parse_spice(PASSIVE, SKY)
    sym = netlist_symmetry(c)
    a = cg.assemble(c, _library(), SKY, axis="vertical", symmetry=sym)
    units = placement_units(a["chains"], c, sym, chain_classes=cg._chain_classes(cg._index()))
    intents = placement_intents(units, sym, axis="vertical")
    assert [tuple(p) for p in intents.pairs] == [tuple(p) for p in a["pairs"]]
    for src, mirror in intents.pairs:
        assert next(u for u in units if u.name == mirror).mirror_of == src
    # No axis, nothing to honour.
    none = placement_intents(units, sym, axis=None)
    assert (none.pairs, none.centred, none.soft) == ([], [], [])

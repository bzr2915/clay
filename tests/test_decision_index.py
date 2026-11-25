"""Retrieval and composition over the classification (design doc §4.7, steps S2-S3).

These tests pin what the declared signatures can and cannot derive today. Two
of them (``KNOWN_GAPS``, ``NOT_DERIVED``) record hand-written glue that still
sits between primitives; later steps are expected to shrink them, and the
tests are to be tightened as they do, never loosened.
"""
from clay.decision import index as ix
from clay.learning import primitive_classification as pc

COMPARATOR_INPUTS = ("NetlistText", "Library", "Pack")
INVERTER_INPUTS = ("NetlistText", "InverterLibrary", "Contract", "Pack")

#: Input types no primitive produces, and where the glue that builds them lives.
KNOWN_GAPS = {
    "ActionPlan": "actions.decide_actions builds plans inline (S5)",
    "Boxes": "inverter flow computes outer boxes inline (S5)",
    "Clearances": "inverter flow computes clearances inline (S5)",
    "Freedom": "built inside block_space; the inverter flow builds it inline (S5)",
    "PartFreedom": "built inside block_space; the inverter flow builds it inline (S5)",
    "Rectangles": "inverter flow computes placement rectangles inline (S5)",
    "Region": "routing obstacles built inline in the routing experiment",
    "SolverConstraints": "parsed from .clay annotations, outside the learning flow",
    "Trials": "relation flow collects trials inline (S5)",
}

#: The primitives the comparator flow calls directly (assemble / evaluate /
#: generate_circuit / circuit_cli), since S3. Primitives called only inside
#: another (build_row, contact_policy_plan, block_from_row, reflect,
#: solve_blocks) are not listed; decisions (fold_candidates) are options, not
#: composition steps.
HARD_CODED = {"netlist_flatten", "netlist_symmetry", "finger_fold", "diffusion_chains",
              "placement_units", "build_chain_row", "build_poly_resistor", "build_mim_cap",
              "turn", "block_space", "mirror_space", "placement_intents", "place_blocks",
              "materialize", "circuit_verify"}

#: Directly called steps the signatures do not derive, and why: both are
#: transforms of a type into itself (Row -> Row, BlockSpace -> BlockSpace),
#: applied by the action space as decisions rather than needed to reach a type.
#: Before S3 this also held finger_fold, diffusion_chains and netlist_symmetry,
#: which reached the flow only through assemble's glue.
NOT_DERIVED = {"turn", "mirror_space"}


def test_dispatch_is_unambiguous():
    assert ix.dispatch_conflicts(ix.build()) == []


def test_a_device_class_finds_its_constructor():
    index = ix.build()
    assert "build_mim_cap" in index.producers("Row", "cap_mim")
    assert "build_mim_cap" not in index.producers("Row", "res_poly")
    assert "build_poly_resistor" in index.by_device_class["res_poly"]


def test_known_gaps_are_exactly_the_hand_written_glue():
    gaps = ix.gaps(ix.build())
    assert set(gaps) == set(KNOWN_GAPS), sorted(set(gaps) ^ set(KNOWN_GAPS))


def test_generation_cannot_reach_a_library_from_a_reference_layout():
    """Generation is given the netlist and the frozen library, never a
    reference layout; the types must make reading one impossible."""
    index = ix.build()
    no_library = ("NetlistText", "Pack", "Packs", "InverterLibrary", "Contract")
    assert "Library" not in ix.producible(index, no_library)
    assert "Library" in ix.producible(index, no_library + ("ReferenceLayout",))


def test_comparator_composition_is_derived_without_choices():
    d = ix.derive(ix.build(), "Verification", given=COMPARATOR_INPUTS)
    assert d.choices == {}
    assert set(d.steps) == HARD_CODED - NOT_DERIVED


def test_derived_composition_differs_from_the_hard_coded_one_only_by_transforms():
    d = ix.derive(ix.build(), "Verification", given=COMPARATOR_INPUTS)
    assert set(d.steps) <= HARD_CODED
    assert HARD_CODED - set(d.steps) == NOT_DERIVED


def test_an_engine_is_never_offered_as_a_step():
    d = ix.derive(ix.build(), "Verification", given=COMPARATOR_INPUTS)
    assert "solve_blocks" not in d.steps


def test_inverter_composition_shows_the_coexisting_choices():
    """With inverter inputs: two checks are feasible (the inverter check and
    the circuit-general one), and two MOS row constructors - build_row, the
    inverter flow's direct step, and the inverter-trained alternative
    constructor (S5). The circuit-general chain constructor is not feasible:
    it needs the circuit library and generation cannot derive one."""
    d = ix.derive(ix.build(), "Verification", given=INVERTER_INPUTS, device_classes=("mos",))
    assert d.choices == {"Row": ("build_learned_pattern_row", "build_row"),
                         "Verification": ("candidate_verify", "circuit_verify")}
    assert {"contact_plan", "build_row", "build_learned_pattern_row"} <= set(d.steps)
    assert "build_chain_row" not in d.steps and "contact_policy_plan" not in d.steps
    assert "fit_library" not in d.steps and "observe_layout" not in d.steps


def test_an_alternative_is_offered_next_to_the_dispatch_default():
    both = COMPARATOR_INPUTS + ("InverterLibrary",)
    d = ix.derive(ix.build(), "Verification", given=both)
    assert d.choices == {"Row": ("build_chain_row", "build_learned_pattern_row")}


def test_every_type_used_is_in_the_vocabulary():
    for item in pc.load().values():
        assert set(item.signature.types()) <= set(pc.TYPES), item.name

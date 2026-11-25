"""The classification must agree with both harness registries.

The failure this prevents is the one that existed before this file: two
registries describing the same primitives in two vocabularies, disagreeing
about the same primitive, with nothing noticing. These tests make the
disagreement a test failure instead of a documentation inconsistency.
"""
import pytest

from clay.learning import primitive_classification as pc
from clay.pdk import semantic


def test_the_file_declares_closed_vocabularies():
    table = pc.load()
    assert table
    for item in table.values():
        assert item.stage in pc.STAGES
        assert item.form in pc.FORMS
        assert item.freedom_effect in pc.FREEDOM_EFFECTS
        assert item.summary.strip()


def test_every_stage_is_used_or_explicitly_empty():
    groups = pc.by_stage(pc.load())
    assert set(groups) == set(pc.STAGES)
    # A stage may be empty in principle, but today none is: if one becomes
    # empty that is worth noticing, not silently tolerated.
    assert all(groups[stage] for stage in pc.STAGES), groups


def test_probe_registry_matches_the_classification():
    import primitive_probe.adapters.flow_decisions  # noqa: F401  (registers)
    import primitive_probe.adapters.learning_flow   # noqa: F401
    import primitive_probe.adapters.symmetry_placer  # noqa: F401
    from primitive_probe.core import REGISTRY as PROBE
    table = pc.load()
    # Probe adapter names -> classification entry. An adapter may expose a
    # narrower view of a classified primitive (solve_area_core, solve_area_relation
    # both show solve_area), which is what the aliases express.
    by_alias = {}
    for item in table.values():
        for alias in item.aliases.get("probe", ()):
            by_alias[alias] = item
    unclassified = sorted(set(PROBE) - set(by_alias))
    assert not unclassified, f"probe adapters missing from the classification: {unclassified}"
    for adapter, item in by_alias.items():
        assert adapter in PROBE, f"classification names probe adapter {adapter!r} that does not exist"
        # form is what a harness reads back; probe's geometry flag is the same
        # distinction expressed as a boolean.
        assert (item.form == "data") == (not PROBE[adapter].geometry), (
            f"{adapter}: form {item.form!r} disagrees with probe geometry flag "
            f"{PROBE[adapter].geometry}")


def test_verify_registry_matches_the_classification():
    from primitive_verify.registry import REGISTRY as VERIFY
    table = pc.load()
    by_alias = {}
    for item in table.values():
        for alias in item.aliases.get("verify", ()):
            by_alias[alias] = item
    unclassified = sorted(set(VERIFY) - set(by_alias))
    assert not unclassified, f"verify primitives missing from the classification: {unclassified}"
    for name, primitive in VERIFY.items():
        assert name in by_alias, name
        assert by_alias[name].form == primitive.form, (
            f"{name}: classification says {by_alias[name].form!r}, "
            f"verify registry says {primitive.form!r}")


def test_declared_rule_bindings_resolve_in_the_pack_they_claim():
    """``pdk_binding`` is a promise; a pack that cannot meet it is a mistake."""
    table = pc.load()
    gf = semantic.get_pack("gf180mcu_5v")
    # Primitives that do not read a pack at all state no binding, so every
    # primitive classified here must be satisfiable by the GF180 pack: it is the
    # reference process the experiment runs on.
    for name in table:
        pc.check_pdk_bindings(table, gf, name)


def test_a_missing_rule_is_reported_with_the_primitive_and_key():
    table = pc.load()
    gf = semantic.get_pack("gf180mcu_5v")
    # A deliberately broken binding names both the primitive and the key.
    broken = dict(table)
    broken["build_row"] = pc.Classification(
        name="build_row", stage="construct", form="layout", freedom_effect="structure",
        pdk_binding=("cut", "no_such_rule"), summary="x", aliases={})
    with pytest.raises(ValueError, match="no_such_rule"):
        pc.check_pdk_bindings(broken, gf, "build_row")


def test_runnable_on_reports_which_primitives_a_pack_can_serve():
    """Cross-PDK readiness is a query, not a reading of the source."""
    table = pc.load()
    for pdk in ("gf180mcu_5v", "sky130_1v8"):
        runnable = pc.runnable_on(table, semantic.get_pack(pdk))
        assert "turn" in runnable          # reads nothing
        assert "build_row" in runnable     # reads only semantic keys
        assert "materialize" in runnable


def test_the_vocabulary_is_closed_against_typos():
    with pytest.raises(ValueError, match="unknown stage"):
        pc.Classification(name="x", stage="planning", form="data",
                          freedom_effect="none", pdk_binding=(), summary="x", aliases={})
    with pytest.raises(ValueError, match="unknown form"):
        pc.Classification(name="x", stage="plan", form="gds",
                          freedom_effect="none", pdk_binding=(), summary="x", aliases={})
    with pytest.raises(ValueError, match="unknown freedom_effect"):
        pc.Classification(name="x", stage="plan", form="data",
                          freedom_effect="transform", pdk_binding=(), summary="x", aliases={})
    with pytest.raises(ValueError, match="unknown decision_role"):
        pc.Classification(name="x", stage="decide", form="data", freedom_effect="none",
                          pdk_binding=(), summary="x", aliases={}, decision_role="solve")


# ---------------------------------------------------------------------------
# decision_role and implementation (GLOBAL_COORDINATOR_DESIGN_zh.md step S1)
# ---------------------------------------------------------------------------


def test_only_decide_primitives_carry_a_decision_role():
    with pytest.raises(ValueError, match="needs stage 'decide'"):
        pc.Classification(name="x", stage="plan", form="data", freedom_effect="none",
                          pdk_binding=(), summary="x", aliases={}, decision_role="bound")
    for item in pc.load().values():
        if item.stage != "decide":
            assert item.decision_role == "none", item.name


def test_declared_implementations_exist_and_are_callable():
    """The table may not describe code that is not there."""
    declared = [item for item in pc.load().values() if item.implementation]
    assert declared
    for item in declared:
        assert callable(pc.resolve(item)), item.name


def test_a_missing_implementation_is_reported():
    bad = pc.Classification(name="x", stage="decide", form="data", freedom_effect="none",
                            pdk_binding=(), summary="x", aliases={},
                            implementation="clay.learning.freedom:no_such_function")
    with pytest.raises(AttributeError):
        pc.resolve(bad)
    with pytest.raises(ValueError, match="module:function"):
        pc.Classification(name="x", stage="decide", form="data", freedom_effect="none",
                          pdk_binding=(), summary="x", aliases={}, implementation="freedom")


def test_every_decision_level_role_has_a_registered_primitive():
    """The decision parts are retrievable by role. ``feedback`` was empty until
    the fixed-orientation fallback left generate_circuit (step S4)."""
    roles = pc.by_decision_role(pc.load())
    assert set(roles) == set(pc.DECISION_ROLES) - {"none"}
    assert "angle_verdicts" in roles["admissibility"]
    assert "enforce" in roles["admissibility"]
    assert {"orientations", "fold_candidates", "netlist_symmetries"} <= set(roles["options"])
    assert {"canonical_rotation_class", "symmetry_classes"} <= set(roles["reduce"])
    assert "two_box_lower_bound" in roles["bound"]
    assert roles["feedback"] == ("verification_feedback",)


def test_the_action_decider_parts_are_classified():
    table = pc.load()
    for name in ("angle_verdicts", "enforce", "orientations", "fold_candidates",
                 "canonical_rotation_class", "two_box_lower_bound", "block_space",
                 "mirror_space"):
        assert name in table, name
        assert table[name].implementation, name


# ---------------------------------------------------------------------------
# capability signatures (GLOBAL_COORDINATOR_DESIGN_zh.md §4.4, step S2)
# ---------------------------------------------------------------------------

#: Modules the determinism proof audited (design doc §15.3, lemma 2). An entry
#: may claim ``determinism: pure`` only if its code lives in one of them.
AUDITED = {
    "clay.pdk.semantic", "clay.learning.netlist", "clay.learning.circuit",
    "clay.learning.symmetry", "clay.learning.diffusion_chains",
    "clay.learning.contact_primitives", "clay.learning.passive_primitives",
    "clay.learning.action_space", "clay.learning.circuit_generate",
    "clay.learning.freedom", "clay.learning.actions", "clay.learning.circuit_verify",
    "clay.learning.contact_observations", "clay.layout.synth.area_placement",
    "clay.learning.rotations", "clay.layout.gds_write",
    # S3 (design doc §15.3, lemma 2 addendum)
    "clay.learning.circuit_construct", "clay.learning.relations",
    "clay.decision.index", "clay.decision.invoke",
}


def _turn_entry(**over):
    base = dict(name="x", stage="transform", form="layout", freedom_effect="rotation",
                pdk_binding=(), summary="x", aliases={},
                implementation="clay.learning.contact_primitives:turn",
                signature=pc.Signature(inputs={"row": "Row"}, params=("n",), output="Row"))
    base.update(over)
    return pc.Classification(**base)


def test_every_signature_matches_its_function():
    """Declared inputs and parameters are real parameters, and no required
    parameter is left undeclared, for every entry in the table."""
    for item in pc.load().values():
        pc.check_signature(item)


def test_a_signature_that_disagrees_with_the_code_is_reported():
    with pytest.raises(ValueError, match="does not take"):
        pc.check_signature(_turn_entry(signature=pc.Signature(
            inputs={"row": "Row"}, params=("n", "angle"), output="Row")))
    with pytest.raises(ValueError, match=r"required parameters \['n'\]"):
        pc.check_signature(_turn_entry(signature=pc.Signature(
            inputs={"row": "Row"}, params=(), output="Row")))


def test_signature_vocabularies_are_closed():
    with pytest.raises(ValueError, match="unknown type"):
        pc.Signature(inputs={"row": "Rows2"}, params=(), output="Row")
    with pytest.raises(ValueError, match="more than once"):
        pc.Signature(inputs={"row": "Row"}, params=("row",), output="Row")
    with pytest.raises(ValueError, match="unknown composition"):
        _turn_entry(composition="choice")
    with pytest.raises(ValueError, match="applies_to"):
        _turn_entry(composition="dispatch")
    with pytest.raises(ValueError, match="unknown device classes"):
        _turn_entry(applies_to=("bjt_npn",))
    with pytest.raises(ValueError, match="alternative_group"):
        _turn_entry(composition="alternative")
    with pytest.raises(ValueError, match="alternative_group"):
        _turn_entry(alternative_group="g")
    with pytest.raises(ValueError, match="options_domain"):
        _turn_entry(options_domain="complete")
    with pytest.raises(ValueError, match="not pure"):
        _turn_entry(mutates=("row",), determinism="pure")


def test_pure_claims_stay_inside_the_audited_proof():
    for item in pc.load().values():
        module = item.implementation.partition(":")[0]
        if item.determinism == "pure":
            assert module in AUDITED, f"{item.name}: claims pure but {module} is unaudited"


def test_option_primitives_say_whether_their_option_set_is_complete():
    table = pc.load()
    assert table["orientations"].options_domain == "complete"
    # Three fold targets out of many: the certificate scope must say so.
    assert table["fold_candidates"].options_domain == "truncated"


def test_a_primitive_that_writes_its_input_says_so():
    assert pc.load()["relation_rank"].mutates == ("trials",)


def test_every_rule_key_the_code_reads_is_declared():
    """Static scan (step S3b): a key the code reads but the table does not
    declare would make 'can this primitive run on this PDK?' answer wrongly.
    The scan is a lower bound (keys passed through variables are not seen),
    so it can only find omissions, which is what this test forbids."""
    for item in pc.load().values():
        found = pc.scan_rule_keys(item)
        declared = set(item.pdk_binding) | set(item.optional_binding)
        assert found <= declared, f"{item.name} reads undeclared keys {sorted(found - declared)}"


def test_the_scan_sees_through_same_module_helpers():
    # build_row reads sd_overhang only inside chain_dimensions, a helper.
    assert "sd_overhang" in pc.scan_rule_keys(pc.load()["build_row"])


# ---------------------------------------------------------------------------
# verification registry: append-only for AI, frozen entries untouchable
# (primitive_verify/README_zh.md §5)
# ---------------------------------------------------------------------------


def _frozen_record():
    import json
    from pathlib import Path
    path = Path(pc.ROOT) / "primitive_verify" / "frozen_entries.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def test_frozen_verification_entries_are_unchanged():
    """A frozen entry may not be edited or removed; approving one means a human
    edits frozen_entries.json as well."""
    from primitive_verify.registry import REGISTRY
    record = _frozen_record()
    frozen = {n: p for n, p in REGISTRY.items() if p.status == "frozen"}
    assert set(frozen) == set(record), sorted(set(frozen) ^ set(record))
    for name, p in frozen.items():
        assert list(p.targets) == record[name]["targets"], name
        assert p.form == record[name]["form"], name


def test_ai_registered_entries_wait_for_review():
    from primitive_verify.registry import REGISTRY
    for p in REGISTRY.values():
        if p.provenance == "ai":
            assert p.status == "pending_review", p.name


def test_review_vocabulary_is_closed():
    from primitive_verify.registry import Primitive
    with pytest.raises(ValueError):
        Primitive("x", ("clay.a:b",), "data", provenance="llm")
    with pytest.raises(ValueError):
        Primitive("x", ("clay.a:b",), "data", status="approved")

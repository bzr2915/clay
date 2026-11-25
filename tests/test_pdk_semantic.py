"""Semantic rule packs: PDK-neutral primitives, verified against two processes.

Two things are asserted here:

1. The GF180 pack reproduces the consolidated numbers the primitives used
   before the refactor, bit for bit. This is the "behaviour did not change"
   gate for the de-coupling.
2. The same primitive code runs on SKY130A and produces a layout that passes
   its own checks, on SKY130 layers, with no GF180-only marker layers.

The SKY130 library below is a hand-written smoke policy, not a learned fit:
it exists to prove the *code path* is process-neutral.
"""
from pathlib import Path

import klayout.db as db
import pytest

from clay.learning import contact_primitives as contacts
from clay.learning.circuit import PlacementContract, parse_cdl, model_polarity
from clay.pdk import semantic

ROOT = Path(__file__).resolve().parents[1]

#: Deck-dependent tests run when the open PDK decks are found
#: (CLAY_GF180MCU_PATH / CLAY_SKY130_PATH, or ./pdks); skipped otherwise.
needs_decks = pytest.mark.skipif(
    not (semantic.get_pack("gf180mcu_5v").deck_sha256 and semantic.get_pack("sky130_1v8").deck_sha256),
    reason="open PDK DRC decks not found; set CLAY_GF180MCU_PATH and CLAY_SKY130_PATH")


# ---------------------------------------------------------------------------
# pack construction
# ---------------------------------------------------------------------------


def test_gf180_pack_reproduces_the_consolidated_numbers():
    """The refactor must not move a single GF180 nanometre."""
    p = semantic.get_pack("gf180mcu_5v")
    expected = {
        # plain rules
        "cut": 220, "cut_space": 250, "cut_to_active": 70, "cut_to_poly": 70,
        "cut_to_gate": 150, "metal_width": 230, "metal_space": 230,
        "metal_area": 144400, "metal_over_cut": 60, "active_space": 360,
        "sd_overhang": 400, "width_min": 300, "poly_width": 200,
        "poly_space": 240, "poly_endcap": 220, "well_width": 860,
        "implant_over_active": 230, "implant_space": 400,
        "implant_area": 350000, "implant_to_opposite_active": 160,
        "oxide_over_active": 240, "oxide_over_poly": 400, "oxide_width": 700,
        "field_poly_active": 300, "gate_cut_to_active": 170,
        # derived quantities that used to be bare literals in build_row
        "cut_half": 110, "cut_pitch": 470, "pad_half_x": 170, "pad_half_y": 215,
        "gate_pad_half": 190, "poly_over_cut": 180, "gate_clear_active": 280,
        "first_sd_contact_x": 180, "side_extension": 440, "shared_segment": 520,
        "opposite_row_clearance": 1200,
    }
    wrong = {k: (p.nm(k), v) for k, v in expected.items() if p.nm(k) != v}
    assert not wrong, f"GF180 pack drifted from the consolidated values: {wrong}"
    assert p.length_rule("nmos") == 600
    assert p.length_rule("pmos") == 500


@needs_decks
def test_every_rule_value_carries_provenance():
    for pdk in ("gf180mcu_5v", "sky130_1v8"):
        p = semantic.get_pack(pdk)
        for key, value in list(p.r.items()) + list(p.derived.items()):
            assert value.provenance in (semantic.Provenance.DECK,
                                        semantic.Provenance.DERIVED,
                                        semantic.Provenance.CONVENTION), key
            assert value.source, f"{pdk}:{key} has no source"
        report = p.report()
        assert report["by_provenance"][semantic.Provenance.DECK] > 0
        # Anything we chose rather than read is listed by name.
        conventions = {c["key"] for c in report["conventions"]}
        for key in conventions:
            assert p.rv(key).provenance == semantic.Provenance.CONVENTION


def test_pack_rejects_incomplete_or_unknown_data():
    p = semantic.get_pack("gf180mcu_5v")
    with pytest.raises(ValueError, match="required rule"):
        semantic.Pack(pdk_id="x", family="x", grid_nm=5, layers=dict(p.layers),
                      r={k: v for k, v in list(p.r.items())[1:]},
                      derived=dict(p.derived), min_length=dict(p.min_length))
    with pytest.raises(ValueError, match="required layer role"):
        semantic.Pack(pdk_id="x", family="x", grid_nm=5,
                      layers={k: v for k, v in p.layers.items() if k != "contact"},
                      r=dict(p.r), derived=dict(p.derived),
                      min_length=dict(p.min_length))


def test_unknown_pdk_is_a_data_error_not_a_silent_default():
    with pytest.raises(KeyError, match="no semantic rule pack"):
        semantic.get_pack("not_a_pdk")


@needs_decks
def test_deck_parser_reads_both_foundry_decks():
    """One parser, two deck dialects: gf180 rule_decks and sky130's .drc."""
    gf = semantic.parse_deck_dir(semantic._find_project_path(semantic.GF180_DECK_DIR))
    assert gf["CO.1"] == (220, False)
    assert gf["M1.3"] == (144400, True)      # area rule, nm^2
    sk = semantic.parse_deck_dir(semantic._find_project_path(semantic.SKY130_DECK_DIR))
    assert sk["licon.1_a/b"] == (170, False)
    assert sk["nwell.2a"] == (1270, False)


# ---------------------------------------------------------------------------
# GF180: unchanged behaviour through the pack-driven primitives
# ---------------------------------------------------------------------------


GF180_CDL = """
.SUBCKT gf180_inv_1 I ZN VDD VNW VPW VSS
M_i_0 ZN I VSS VPW nfet_05v0 W=8.2e-07 L=6e-07
M_i_1 ZN I VDD VNW pfet_05v0 W=1.22e-06 L=5e-07
.ENDS
"""

SKY130_CDL = """
.SUBCKT sky_inv_1 I ZN VDD VNW VPW VSS
M_i_0 ZN I VSS VPW nfet_01v8 W=1.0e-06 L=1.5e-07
M_i_1 ZN I VDD VNW pfet_01v8 W=1.0e-06 L=1.5e-07
.ENDS
"""


def _smoke_library(pack):
    """A hand-written policy: exercises the code path, is not a learned fit."""
    return {
        "schema": contacts.SCHEMA, "pdk": pack.pdk_id,
        "rule_pack": {"pdk": pack.pdk_id, "grid_nm": pack.nm("grid"),
                      "cut_pitch_nm": pack.nm("cut_pitch"),
                      "deck_sha256": pack.deck_sha256},
        "patterns": {k: [{"gate_count": 1, "segments": ["rail", "output"]}]
                     for k in ("nmos", "pmos")},
        "contact_policies": {k: {"rows": {"kind": "constant", "count": 1},
                                 "first_contact_free_margin_fraction": .5}
                             for k in ("nmos", "pmos")},
        "binding": "target_W_L_models_and_nets",
        "operator": "rectangular_diffusion_chain_v1",
    }


def _contract(pack):
    return PlacementContract("VDD", "VSS", "VPW", "VNW", pdk=pack.pdk_id,
                             floorplan="free_single_pair_rotations",
                             device_recipe=pack.recipe("contacts"),
                             grid_nm=pack.nm("grid"), solver_seconds=5)


def test_gf180_layout_is_reproduced_by_the_pack_driven_primitive(tmp_path):
    """Same input, same output: the de-coupled primitive is behaviour-preserving."""
    pack = semantic.get_pack("gf180mcu_5v")
    circuit = parse_cdl(GF180_CDL)
    contract = _contract(pack)
    out = tmp_path / "gf180.gds"
    result = contacts.generate(_smoke_library(pack), circuit, contract, out, pack=pack)
    assert result["pdk"] == "gf180mcu_5v"
    assert result["best"]["verification"]["passed"]
    layout = db.Layout()
    layout.read(str(out))
    layers = {(layout.get_info(i).layer, layout.get_info(i).datatype)
              for i in layout.layer_indexes()}
    # The GF180 marker layers are present because the pack declares them.
    assert (55, 0) in layers and (112, 1) in layers


# ---------------------------------------------------------------------------
# SKY130: the same primitives, a different process
# ---------------------------------------------------------------------------


def test_sky130_pack_switches_layers_and_drops_absent_markers():
    gf = semantic.get_pack("gf180mcu_5v")
    sk = semantic.get_pack("sky130_1v8")
    assert sk.layer("contact") == (66, 44)     # licon
    assert sk.layer("metal1") == (67, 20)      # li
    assert gf.layer("contact") != sk.layer("contact")
    # sky130A has no dualgate / 5 V selector, so the pack must not claim one.
    assert sk.markers == ()
    assert "dualgate" not in sk.layers and "v5_xtor" not in sk.layers


def test_the_same_primitive_generates_a_legal_sky130_layout(tmp_path):
    """The decisive test: no GF180 number or layer reaches this output."""
    pack = semantic.get_pack("sky130_1v8")
    circuit = parse_cdl(SKY130_CDL)
    contract = _contract(pack)
    out = tmp_path / "sky130.gds"
    result = contacts.generate(_smoke_library(pack), circuit, contract, out, pack=pack)

    assert result["pdk"] == "sky130_1v8"
    verification = result["best"]["verification"]
    assert verification["passed"], verification["failed"]
    assert verification["pdk"] == "sky130_1v8"

    layout = db.Layout()
    layout.read(str(out))
    layers = {(layout.get_info(i).layer, layout.get_info(i).datatype)
              for i in layout.layer_indexes()}
    # Drawn on SKY130 layers...
    for role in ("active", "poly", "contact", "metal1", "nwell", "nplus", "pplus"):
        assert pack.layer(role) in layers, role
    # ...and with none of the GF180-only layers.
    assert (55, 0) not in layers and (112, 1) not in layers
    assert (22, 0) not in layers and (33, 0) not in layers

    # Every cut is the SKY130 licon size, not GF180's 220 nm.
    cut = pack.nm("cut")
    top = layout.top_cell()
    contacts_region = db.Region(top.begin_shapes_rec(layout.layer(*pack.layer("contact"))))
    assert contacts_region.count() > 0
    for q in contacts_region.each():
        assert q.bbox().width() == cut and q.bbox().height() == cut


def test_library_fitted_for_one_process_cannot_apply_to_another():
    """Cross-PDK is not automatic transfer: an nm-valued library is rejected."""
    gf = semantic.get_pack("gf180mcu_5v")
    sk = semantic.get_pack("sky130_1v8")
    library = _smoke_library(gf)
    with pytest.raises(ValueError, match="refit required"):
        contacts.validate_library(library, pack=sk)
    # ...and it still validates against the pack it was written for.
    contacts.validate_library(library, pack=gf)


def test_unbound_device_model_fails_closed():
    """A pack binds its models; a 3.3 V nfet must not pass as a 5 V device."""
    pack = semantic.get_pack("gf180mcu_5v")
    circuit = parse_cdl(GF180_CDL.replace("nfet_05v0", "nfet_03v3"))
    with pytest.raises(KeyError, match="not bound to a polarity"):
        for d in circuit.devices:
            pack.polarity(d.model)


def test_model_polarity_is_naming_based_not_a_pdk_table():
    assert model_polarity("nfet_05v0") == "nmos"
    assert model_polarity("pfet_05v0") == "pmos"
    assert model_polarity("sky130_fd_pr__nfet_01v8") == "nmos"
    assert model_polarity("sky130_fd_pr__pfet_01v8_hvt") == "pmos"
    with pytest.raises(ValueError, match="cannot infer polarity"):
        model_polarity("resistor_1000")


# ---------------------------------------------------------------------------
# C1: per-layer geometric freedom, parsed from the decks' angle rules
# ---------------------------------------------------------------------------


@needs_decks
def test_angle_rules_are_parsed_from_both_decks():
    gf = semantic.parse_deck_angles_dir(semantic._find_project_path(semantic.GF180_DECK_DIR))
    sk = semantic.parse_deck_angles_dir(semantic._find_project_path(semantic.SKY130_DECK_DIR))
    # gf180 states every angle rule as "non 45 degree": 45-degree edges allowed.
    assert gf["comp"][0] == 45 and gf["poly2"][0] == 45 and gf["metal1"][0] == 45
    # sky130 mixes both: rectangles only for diff/poly/licon, 45 for metal.
    assert sk["diff"][0] == 90 and sk["poly"][0] == 90 and sk["licon"][0] == 90
    assert sk["li"][0] == 45 and sk["nwell"][0] == 45


@needs_decks
def test_repeated_angle_rule_takes_the_stricter_one():
    """sky130 states diff twice (90 general, 45 inside uhvi): keep 90."""
    sk = semantic.parse_deck_angles_dir(semantic._find_project_path(semantic.SKY130_DECK_DIR))
    assert sk["diff"] == (90, 90)
    assert sk["tap"] == (90, 90)
    # The looser region-specific value must not survive on its own.
    assert all(step >= 90 for token, (step, _hi) in sk.items()
               if token in ("diff", "tap"))


@needs_decks
def test_freedom_is_per_layer_not_per_row():
    """This is the reviewer's point: one row spans layers with different freedom."""
    sk = semantic.get_pack("sky130_1v8")
    assert sk.freedom["poly"].rotations_deg == (0, 90, 180, 270)
    assert sk.freedom["contact"].rotations_deg == (0, 90, 180, 270)
    assert sk.freedom["active"].rotations_deg == (0, 90, 180, 270)
    # A well may sit at 45 degrees even though the diffusion inside it may not.
    assert sk.freedom["nwell"].allows_45
    assert not sk.freedom["poly"].allows_45


@needs_decks
def test_gf180_gives_every_modelled_layer_45_degree_freedom():
    gf = semantic.get_pack("gf180mcu_5v")
    for role in ("active", "poly", "contact", "metal1", "nwell",
                 "nplus", "pplus", "dualgate", "v5_xtor"):
        assert gf.freedom[role].step_deg == 45, role
        assert gf.freedom[role].provenance == semantic.Provenance.DECK


def test_a_layer_with_no_angle_rule_has_no_freedom_entry():
    """Absent must mean 'unknown, so not free', never 'unrestricted'."""
    gf = semantic.get_pack("gf180mcu_5v")
    # metal2 is modelled by the pack but has no role mapping in the GF180
    # angle vocabulary, so no freedom may be invented for it.
    for role, freedom in gf.freedom.items():
        assert freedom.source, role
    assert "metal2" not in gf.freedom


@needs_decks
def test_engine_capability_is_declared_separately_from_pdk_freedom():
    """The deck may permit 45 degrees; the engine may not yet verify it."""
    for pdk in ("gf180mcu_5v", "sky130_1v8"):
        pack = semantic.get_pack(pdk)
        assert "rotate_orthogonal" in pack.capability
        assert "rotate_45" not in pack.capability
        # Freedom exists that capability cannot yet execute: declared, not used.
        assert any(f.allows_45 for f in pack.freedom.values())


def test_layer_freedom_accepts_any_step_that_tiles_a_turn():
    """A deck may state any increment; only a non-tiling one is impossible."""
    for step in (1, 5, 15, 30, 45, 60, 90, 120, 180):
        freedom = semantic.LayerFreedom(step, semantic.Provenance.CONVENTION, "test")
        assert len(freedom.rotations_deg) == 360 // step
        assert freedom.allows(0) and freedom.allows(step)
    for step in (7, 50, 120 + 1):
        with pytest.raises(ValueError, match="does not tile a full turn"):
            semantic.LayerFreedom(step, semantic.Provenance.CONVENTION, "test")
    with pytest.raises(ValueError, match="positive integer"):
        semantic.LayerFreedom(0, semantic.Provenance.CONVENTION, "test")

"""Mechanical command-line flow for circuit-level primitive learning.

Three steps, each reading only what it is allowed to read:

    python -m clay.learning.circuit_cli train    --contract C --output LIB.json
        reads the *training* entries of the contract (netlist + reference GDS)
    python -m clay.learning.circuit_cli generate --netlist N --pdk P --library LIB.json --output OUT.gds
        reads one netlist and the frozen library; never a reference layout.
        Runs the coordinator core: symmetry mapping, construction and placement
        are chosen by --objective (default area); --fixed-symmetry keeps the
        rule-picked mapping
    python -m clay.learning.circuit_cli evaluate --generated OUT.gds --reference REF.gds --pdk P
        compares areas *after* generation; the reference never flows back

A contract is JSON: ``{"samples": [{"id", "split": "training"|"test", "pdk",
"netlist", "top"?, "net_alias"?, "reference_gds"}]}``, paths relative to the
contract file. Nothing is guessed: a netlist dialect detail (an extracted
netlist's terminal-stub suffix) is stated in ``net_alias``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import klayout.db as db

from clay.learning.circuit_generate import generate_circuit
from clay.learning.circuit_library import (DEVICE_PLANE_ROLES, fit_library, frame_bbox,
                                           load_library, material_bbox, observe_layout,
                                           read_gds, save_library, sha256)
from clay.learning.netlist import parse_spice, summary
from clay.pdk.semantic import get_pack, require_deck


def load_contract(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for s in data["samples"]:
        for key in ("netlist", "reference_gds"):
            if s.get(key):
                s[key] = str((path.parent / s[key]).resolve())
        if s["split"] not in ("training", "test"):
            raise ValueError(f"{s['id']}: split must be training or test")
    return data


def parse_sample(sample):
    pack = require_deck(get_pack(sample["pdk"]))
    text = Path(sample["netlist"]).read_text(encoding="utf-8", errors="replace")
    return parse_spice(text, pack, top=sample.get("top"), net_alias=sample.get("net_alias")), pack


def train(contract, output, *, log=print):
    obs, packs = [], {}
    for s in contract["samples"]:
        if s["split"] != "training":
            continue
        circuit, pack = parse_sample(s)
        packs[pack.pdk_id] = pack
        o = observe_layout(s["id"], s["reference_gds"], circuit, pack)
        log(f"observed {s['id']}: {o['gates']} gates, contact examples {len(o['contacts'])}, "
            f"best axis {o['best_axis']}")
        obs.append(o)
    if not obs:
        raise ValueError("contract has no training samples")
    lib = fit_library(obs, packs)
    save_library(lib, output)
    return lib, obs


def generate(netlist, pdk, library, output, *, top=None, net_alias=None, quick_seconds=15.0,
             final_seconds=60.0, finalists=2, workers=8, objective="area",
             search_symmetry=True, inverter_library=None, log=print):
    """Generate through the coordinator core (design doc §6): the symmetry
    mapping, the construction and the placement are chosen by ``objective``.
    ``search_symmetry=False`` keeps the mapping netlist_symmetry's rule picks,
    which is exactly the pre-coordinator generate_circuit.
    ``inverter_library`` (path or dict of an inverter contact library) makes
    the inverter-trained construction an alternative constructor (S5)."""
    del finalists
    from clay.decision.core import coordinate
    pack = require_deck(get_pack(pdk))
    circuit = parse_spice(Path(netlist).read_text(encoding="utf-8", errors="replace"), pack,
                          top=top, net_alias=net_alias)
    lib = load_library(library) if not isinstance(library, dict) else library
    held = None
    if inverter_library is not None:
        held = {"InverterLibrary": inverter_library if isinstance(inverter_library, dict)
                else json.loads(Path(inverter_library).read_text(encoding="utf-8"))}
    log(f"generate {circuit.name}: {summary(circuit)}")
    result = coordinate(circuit, lib, pack, output, objective=objective,
                        search_symmetry=search_symmetry, quick_seconds=quick_seconds,
                        final_seconds=final_seconds, workers=workers, held=held, log=log)
    result["netlist"] = {"path": str(netlist), "sha256": sha256(netlist),
                         "summary": summary(circuit)}
    result["library_sha256"] = sha256(library) if not isinstance(library, dict) else None
    return result


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1]) if b else None


def evaluate(generated, reference, pdk):
    """Area comparison and a geometric identity check, after generation."""
    pack = require_deck(get_pack(pdk))
    gen, ref = read_gds(generated), read_gds(reference)
    g_mat = material_bbox(gen, pack)
    r_mat = material_bbox(ref, pack)
    r_dev = material_bbox(ref, pack, DEVICE_PLANE_ROLES)
    r_frame = frame_bbox(ref, pack)
    # Identity: overlay the device-plane layers with both lower-left corners
    # at the origin and measure the XOR. 0 would mean an identical layout.
    xor_area = union_area = 0
    from clay.learning.circuit_library import layout_regions
    G, Rg = layout_regions(gen, pack), layout_regions(ref, pack)
    shift_g = db.Trans(-g_mat[0], -g_mat[1])
    shift_r = db.Trans(-r_mat[0], -r_mat[1])
    per_layer = {}
    for role in ("active", "tap", "poly", "contact", "metal1", "nwell"):
        if role not in G:
            continue
        a = G[role].transformed(shift_g)
        b = Rg[role].transformed(shift_r)
        x = (a ^ b).area()
        u = (a | b).area()
        xor_area += x
        union_area += u
        per_layer[role] = {"xor_nm2": x, "union_nm2": u}
    out = {
        "generated_material_bbox_nm": g_mat, "generated_area_nm2": _area(g_mat),
        "reference_material_bbox_nm": r_mat, "reference_material_area_nm2": _area(r_mat),
        "reference_device_plane_bbox_nm": r_dev, "reference_device_plane_area_nm2": _area(r_dev),
        "reference_frame_bbox_nm": r_frame, "reference_frame_area_nm2": _area(r_frame),
        "ratio_to_material": _area(g_mat) / _area(r_mat),
        "ratio_to_device_plane": _area(g_mat) / _area(r_dev) if r_dev else None,
        "smaller_than_reference": _area(g_mat) < _area(r_mat),
        "xor_fraction_device_layers": xor_area / union_area if union_area else None,
        "identical": xor_area == 0,
        "per_layer": per_layer,
        "generated_sha256": sha256(generated), "reference_sha256": sha256(reference),
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m clay.learning.circuit_cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--contract", required=True)
    t.add_argument("--output", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--netlist", required=True)
    g.add_argument("--pdk", required=True)
    g.add_argument("--library", required=True)
    g.add_argument("--output", required=True)
    g.add_argument("--top")
    g.add_argument("--net-alias")
    g.add_argument("--quick-seconds", type=float, default=15.0)
    g.add_argument("--final-seconds", type=float, default=60.0)
    g.add_argument("--workers", type=int, default=8)
    g.add_argument("--objective", default="area",
                   help="declared objective of the coordinator: area | matched_pairs | centring")
    g.add_argument("--fixed-symmetry", action="store_true",
                   help="do not search symmetry mappings; use netlist_symmetry's rule")
    g.add_argument("--inverter-library",
                   help="inverter contact library: its learned construction becomes an "
                        "alternative MOS chain constructor")
    e = sub.add_parser("evaluate")
    e.add_argument("--generated", required=True)
    e.add_argument("--reference", required=True)
    e.add_argument("--pdk", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "train":
        lib, _ = train(load_contract(args.contract), args.output)
        print(json.dumps({k: lib[k] for k in ("contact_policies", "relations")}, indent=2))
    elif args.cmd == "generate":
        r = generate(args.netlist, args.pdk, args.library, args.output, top=args.top,
                     net_alias=args.net_alias, quick_seconds=args.quick_seconds,
                     final_seconds=args.final_seconds, workers=args.workers,
                     objective=args.objective, search_symmetry=not args.fixed_symmetry,
                     inverter_library=args.inverter_library)
        Path(args.output).with_suffix(".json").write_text(
            json.dumps(r, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"{r['cell']}: {r['best']['area_nm2'] / 1e6:.3f} um2, "
              f"verification {'PASS' if r['best']['verification']['passed'] else 'FAIL'}")
    else:
        print(json.dumps(evaluate(args.generated, args.reference, args.pdk), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

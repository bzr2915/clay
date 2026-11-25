"""Comparator learning, end to end: train -> generate (audited) -> evaluate -> report.

    python scripts/run_comparator_learning.py [--contract PATH] [--output DIR]
                                              [--quick-seconds S] [--final-seconds S]

Steps
-----
1. ``train``: observe every *training* sample (netlist + reference GDS) and
   freeze ``library.json``.
2. ``generate``: for every sample, a fresh Python process with an audit hook
   that refuses to open any reference layout (GDS/Magic/extracted LVS netlist of
   *any* sample) runs ``circuit_cli.generate`` from the netlist and the frozen
   library only. The list of files the process opened is kept as evidence.
3. ``baseline``: the same constructors and solver, but in the training layouts'
   own style (one diffusion island per device, no extra folding). This is the
   same-scope comparison: placement only, no routing, on both sides.
4. ``evaluate``: only now is each reference GDS read, to measure its area and
   the XOR overlap against the generated layout.
5. Report: ``results.json`` and ``RESULTS_zh.md`` plus PNG renders.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONTRACT = ROOT / "data" / "primitive_generalization" / "comparator" / "contract.json"
DEFAULT_OUT = ROOT / "data" / "primitive_generalization" / "comparator"


def _forbidden(contract):
    """Every reference layout artefact of every sample (generation must not read them)."""
    out = set()
    for s in contract["samples"]:
        ref = Path(s["reference_gds"])
        out.add(str(ref.resolve()).lower())
        sample_dir = ref.parent.parent
        for pattern in ("**/*.gds", "**/*.gds.gz", "**/*.mag", "**/*lvs*.spice", "**/*pex*"):
            for f in sample_dir.glob(pattern):
                if str(f.resolve()).lower() != str(Path(s["netlist"]).resolve()).lower():
                    out.add(str(f.resolve()).lower())
    return sorted(out)


def audited_generate(args):
    """Child process: install the audit hook, then generate."""
    forbidden = set(json.loads(Path(args.forbidden).read_text(encoding="utf-8")))
    opened = []

    def hook(event, a):
        if event == "open" and a and isinstance(a[0], (str, bytes, Path)):
            p = str(Path(a[0] if not isinstance(a[0], bytes) else a[0].decode())).lower()
            try:
                p = str(Path(p).resolve()).lower()
            except OSError:
                pass
            if p in forbidden:
                raise PermissionError(f"generation tried to read a reference layout: {p}")
            if "comparator_learning_v1" in p:
                opened.append(p)

    sys.addaudithook(hook)
    from clay.learning.circuit_cli import generate
    r = generate(args.netlist, args.pdk, args.library, args.gds, top=args.top or None,
                 net_alias=args.net_alias or None, quick_seconds=args.quick_seconds,
                 final_seconds=args.final_seconds, workers=args.workers,
                 inverter_library=args.inverter_library or None)
    r["audit"] = {"forbidden_files": len(forbidden), "dataset_files_opened": sorted(set(opened))}
    Path(args.gds).with_suffix(".json").write_text(
        json.dumps(r, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def render(gds, png, width=1400, height=900):
    import klayout.lay as lay
    view = lay.LayoutView()
    view.load_layout(str(gds))
    view.max_hier()
    view.zoom_fit()
    view.save_image(str(png), width, height)


def baseline(sample, library, out_dir, seconds, workers):
    """Training-style construction (no sharing, no extra folding), best axis/MIM."""
    from clay.learning.circuit_cli import parse_sample
    from clay.learning.circuit_generate import evaluate
    from clay.learning.symmetry import netlist_symmetry
    circuit, pack = parse_sample(sample)
    sym = netlist_symmetry(circuit)
    best = None
    axes = ["vertical", "horizontal"] if sym else [None]
    mims = [False, True] if any(d.cls == "cap_mim" for d in circuit.devices) else [False]
    trials = []
    for ax in axes:
        for mt in mims:
            cand = {"fold_target": None, "axis": ax, "mim_turn": mt, "share": False}
            # The training-style baseline keeps the old fixed orientations
            # (identity, MIM 0/90 as a candidate): orientation search is part
            # of the generator being compared against it, not of the baseline.
            r, layout, _ = evaluate(circuit, library, pack, cand, seconds=seconds,
                                    workers=workers, symmetry=sym, orient=False)
            trials.append({"candidate": cand, "area_nm2": r["area_nm2"],
                           "passed": r["verification"]["passed"]})
            if r["verification"]["passed"] and (best is None or r["area_nm2"] < best[0]["area_nm2"]):
                best = (r, layout)
    path = out_dir / "baseline_training_style.gds"
    if best is None:
        # Every training-style candidate failed its layout check. Record that
        # instead of crashing the whole run; the generated result stands alone.
        return {"best": None, "trials": trials, "gds": None}
    best[1].write(str(path))
    return {"best": {k: v for k, v in best[0].items() if k not in ("origins_nm",)},
            "trials": trials, "gds": str(path)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    ap.add_argument("--output", default=str(DEFAULT_OUT))
    ap.add_argument("--quick-seconds", type=float, default=15.0)
    ap.add_argument("--final-seconds", type=float, default=60.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--audited-generate", action="store_true", help=argparse.SUPPRESS)
    for k in ("netlist", "pdk", "library", "gds", "top", "net-alias", "forbidden",
              "inverter-library"):
        ap.add_argument(f"--{k}", default="")
    args = ap.parse_args(argv)
    if args.audited_generate:
        return audited_generate(args)

    from clay.learning.circuit_cli import evaluate, load_contract, train
    from clay.learning.circuit_library import load_library
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    contract = load_contract(args.contract)
    t0 = time.time()
    print("== train")
    lib, obs = train(contract, out / "library.json")
    (out / "observations.json").write_text(json.dumps(obs, indent=2, default=str), encoding="utf-8")
    forbidden = _forbidden(contract)
    (out / "forbidden_reference_files.json").write_text(json.dumps(forbidden, indent=2),
                                                        encoding="utf-8")
    results = {"library": str(out / "library.json"), "samples": []}
    for s in contract["samples"]:
        sdir = out / "generated" / s["id"]
        sdir.mkdir(parents=True, exist_ok=True)
        gds = sdir / "layout.gds"
        print(f"== generate {s['id']} ({s['split']})")
        cmd = [sys.executable, str(Path(__file__).resolve()), "--audited-generate",
               "--netlist", s["netlist"], "--pdk", s["pdk"], "--library", str(out / "library.json"),
               "--gds", str(gds), "--top", s.get("top") or "", "--net-alias", s.get("net_alias") or "",
               "--forbidden", str(out / "forbidden_reference_files.json"),
               "--quick-seconds", str(args.quick_seconds), "--final-seconds", str(args.final_seconds),
               "--workers", str(args.workers)]
        subprocess.run(cmd, check=True, cwd=str(ROOT))
        gen = json.loads(gds.with_suffix(".json").read_text(encoding="utf-8"))
        print(f"== baseline {s['id']}")
        base = baseline(s, load_library(out / "library.json"), sdir, args.quick_seconds,
                        args.workers)
        print(f"== evaluate {s['id']}")
        ev = evaluate(gds, s["reference_gds"], s["pdk"])
        ev_base = evaluate(base["gds"], s["reference_gds"], s["pdk"]) if base["gds"] else None
        render(gds, sdir / "layout.png")
        if base["gds"]:
            render(base["gds"], sdir / "baseline_training_style.png")
        render(s["reference_gds"], sdir / "reference.png")
        results["samples"].append({
            "id": s["id"], "split": s["split"], "pdk": s["pdk"],
            "generated": {"gds": str(gds), "area_nm2": gen["best"]["area_nm2"],
                          "candidate": gen["best"]["candidate"],
                          "verification_passed": gen["best"]["verification"]["passed"],
                          "check_count": gen["best"]["verification"]["check_count"],
                          "chains": gen["best"]["chains"], "solver": gen["best"]["solver"],
                          "candidates": [{"candidate": c["candidate"],
                                          "area_nm2": c.get("area_nm2"),
                                          "passed": c.get("verification", {}).get("passed"),
                                          "error": c.get("error")} for c in gen["candidates"]],
                          "audit": gen["audit"], "netlist": gen["netlist"],
                          "decider": gen.get("decider"),
                          "coordinator": gen.get("coordinator")},
            "baseline_training_style": {"area_nm2": base["best"]["area_nm2"] if base["best"] else None,
                                        "candidate": base["best"]["candidate"] if base["best"] else None,
                                        "verification_passed": bool(base["best"]),
                                        "trials": base["trials"]},
            "evaluation": ev, "evaluation_baseline": ev_base})
        print(f"   generated {ev['generated_area_nm2'] / 1e6:.2f} um2 vs reference "
              f"{ev['reference_material_area_nm2'] / 1e6:.2f} um2 "
              f"(ratio {ev['ratio_to_material']:.3f}); baseline "
              + (f"{ev_base['generated_area_nm2'] / 1e6:.2f} um2" if ev_base
                 else "none (no training-style candidate passed verification)"))
    results["seconds"] = time.time() - t0
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"done in {results['seconds']:.0f} s -> {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

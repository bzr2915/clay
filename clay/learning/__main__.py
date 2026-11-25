"""Fresh-process inference with an explicit Python file-read allowlist.

This is an audit guard, not an OS sandbox: native KLayout reads only the newly
written output GDS in generate_placement's roundtrip validation.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import klayout.db as db

from clay.learning.circuit import parse_cdl, PlacementContract
from clay.pdk import semantic
from clay.learning.primitive_rules import load_library
from clay.learning.generate import generate_placement
from clay.learning.construction_search import SCHEMA, search_constructions
from clay.learning.multisample import SCHEMA as STRUCTURAL_SCHEMA, parse_training_cdl, execute_structural_primitive
from clay.learning.contact_primitives import (SCHEMA as CONTACT_SCHEMA,
                                             V1_SCHEMA as CONTACT_SCHEMA_V1,
                                             generate as generate_contacts)
CONTACT_SCHEMAS = (CONTACT_SCHEMA, CONTACT_SCHEMA_V1)
from clay.learning.geometry_primitives import SCHEMA as GEOMETRY_SCHEMA, generate as generate_geometry
from clay.learning.placement_relations import SCHEMA as RELATION_SCHEMA, generate as generate_relations
from clay.learning.routing_primitives import SCHEMA as ROUTING_SCHEMA, generate as generate_routing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-budget", type=int, default=16)
    parser.add_argument("--relation-weight",type=float,default=None)
    parser.add_argument("--wirelength-weight",type=float,default=None)
    parser.add_argument('--neutral-routing',action='store_true',help='Ablate learned width/direction statistics')
    parser.add_argument('--trace-file',type=Path,default=None,help='Write actual primitive-composition execution trace (relation library only)')
    parser.add_argument('--primitive-probe',type=str,default=None,
                        help='Export one generated shape: PRIMITIVE:nmos|pmos:INDEX (contact/geometry/relation schemas)')
    args = parser.parse_args()
    inputs = {p.resolve() for p in (args.netlist, args.library, args.contract)}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    generated_files = {output/"placement.gds", output/"routed.gds", output/"result.json"}
    probe_gds=output/'primitive_probe.gds'
    if args.primitive_probe:
        generated_files.update({probe_gds,probe_gds.with_suffix('.json')})
    trace_file=args.trace_file.resolve() if args.trace_file else None
    if trace_file: generated_files.add(trace_file)
    package = Path(__file__).resolve().parents[1]
    python_root = Path(sys.prefix).resolve()
    reads = set()
    def audit(event, values):
        if event != "open" or not isinstance(values[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(values[0])).resolve()
        mode = values[1]
        flags = values[2] if len(values) > 2 else 0
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR))
        if writing:
            if path not in generated_files:
                raise PermissionError(f"Inference write outside output: {path}")
            return
        if path in inputs or path in generated_files or path.is_relative_to(python_root) or (path.is_relative_to(package) and path.suffix in {".py", ".pyc", ".pyd"}):
            reads.add(str(path))
            return
        raise PermissionError(f"Inference read outside allowlist: {path}")
    sys.dont_write_bytecode = True
    # The PDK is a declared input like the netlist: build the contract's pack
    # (which parses that PDK's DRC deck) before the read allowlist is enforced,
    # and record which deck it came from in the audit below.
    pdk_pack = semantic.get_pack(json.loads(args.contract.read_text(encoding="utf-8"))["pdk"])
    sys.addaudithook(audit)
    netlist_text = args.netlist.read_text(encoding="utf-8")
    library = json.loads(args.library.read_text(encoding="utf-8"))
    contract = PlacementContract(**json.loads(args.contract.read_text(encoding="utf-8")))
    if args.neutral_routing and library.get('schema')!=ROUTING_SCHEMA:
        raise ValueError('Neutral routing requires a routing library')
    probe_schemas={CONTACT_SCHEMA,GEOMETRY_SCHEMA,RELATION_SCHEMA}
    if args.primitive_probe and library.get('schema') not in probe_schemas:
        raise ValueError('Primitive probe is supported only by contact, geometry, and relation generators')
    if library.get('schema')!=RELATION_SCHEMA and (args.relation_weight is not None or args.wirelength_weight is not None):
        raise ValueError('Ranking weights require a relation library')
    recorder=None
    if library.get('schema')==ROUTING_SCHEMA:
        if args.candidate_budget!=16: raise ValueError('Routing uses all 16 placement rotations')
        circuit=parse_cdl(netlist_text)
        result=generate_routing(library,circuit,contract,output/'placement.gds',output/'routed.gds',neutral=args.neutral_routing)
        metrics=result
    elif library.get("schema") == RELATION_SCHEMA:
        from clay.learning.placement_relations import DEFAULT_WEIGHTS
        weights=dict(DEFAULT_WEIGHTS)
        if args.relation_weight is not None: weights['relations']=args.relation_weight
        if args.wirelength_weight is not None: weights['wirelength_proxy']=args.wirelength_weight
        if args.candidate_budget!=16: raise ValueError('Relation ranking evaluates all 16 rotations')
        circuit=parse_cdl(netlist_text)
        recorder=None
        if trace_file:
            from clay.learning.composition_trace import Recorder
            recorder=Recorder(library,circuit,contract,netlist_text,weights)
        result=generate_relations(library,circuit,contract,output/'placement.gds',weights=weights,recorder=recorder,
                                  primitive_probe=args.primitive_probe,probe_output=probe_gds if args.primitive_probe else None)
        metrics=result['best']
    elif library.get("schema") == GEOMETRY_SCHEMA:
        if args.candidate_budget != 16:
            raise ValueError("Geometry primitive uses exhaustive 16-rotation search")
        circuit = parse_cdl(netlist_text)
        result = generate_geometry(library,circuit,contract,output/"placement.gds",
                                   primitive_probe=args.primitive_probe,probe_output=probe_gds if args.primitive_probe else None)
        metrics = result["best"]
    elif library.get("schema") in CONTACT_SCHEMAS:
        # A v1 contact library is upgraded explicitly by generate_contacts; it is
        # not silently reinterpreted, and the upgrade is recorded in the result.
        circuit = parse_cdl(netlist_text)
        # A budget below the full 16 only means something if equivalent
        # rotations are not counted twice. At the full budget the exhaustive
        # sweep is kept, so recorded artefacts retain their 16 trials.
        # The contract names the process; without pack= the generator would
        # fall back to the default (GF180) pack whatever the contract says.
        result = generate_contacts(library,circuit,contract,output/"placement.gds",
                                   pack=semantic.get_pack(contract.pdk),
                                   primitive_probe=args.primitive_probe,probe_output=probe_gds if args.primitive_probe else None,
                                   candidate_budget=args.candidate_budget,
                                   dedupe_rotations=args.candidate_budget < 16)
        metrics = result["best"]
    elif library.get("schema") == STRUCTURAL_SCHEMA:
        # Gate on the pack's declared capability, not on a process name. The
        # adapter id ('gf180_5v') is still the training-adapter key, so it is
        # derived from the contract instead of being written twice.
        _pack = semantic.get_pack(contract.pdk)
        if "structural_lowering" not in _pack.capability:
            raise ValueError(f"pack {_pack.pdk_id!r} has no verified generation provider "
                             "for the structural primitive")
        circuit = parse_training_cdl(netlist_text, contract.pdk)
        if args.candidate_budget != 16:
            raise ValueError("Structural primitive has no trained budgeted selector; use full 16-candidate search")
        result = execute_structural_primitive(library,circuit,contract,output/"placement.gds")
        metrics = result["best"]
    elif library.get("schema") == SCHEMA:
        circuit = parse_cdl(netlist_text)
        result = search_constructions(circuit, contract, library, budget=args.candidate_budget, output=output/"placement.gds")
        metrics = result["best"]
    else:
        circuit = parse_cdl(netlist_text)
        load_library(library)
        result = generate_placement(circuit, library, contract, output/"placement.gds")
        metrics = result
    if args.primitive_probe:
        result['primitive_probe']={**(result.get('primitive_probe') or {}),
                                   'gds':str(probe_gds),
                                   'metadata':str(probe_gds.with_suffix('.json'))}
    result["inference_audit"] = {"allowed_data_inputs": sorted(str(p) for p in inputs),
                                 "pdk_pack": {"pdk_id": pdk_pack.pdk_id, "deck_source": pdk_pack.source,
                                              "deck_sha256": pdk_pack.deck_sha256},
                                 "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                                 "python_file_reads": sorted(reads),
                                 "reference_path_supplied": False,
                                 "scope": "fresh Python process + file read allowlist, not an OS/native-I/O sandbox"}
    if recorder is not None:
        generated_layout=db.Layout();generated_layout.read(str(output/'placement.gds'))
        trace=recorder.finish(result,generated_layout,result['inference_audit'],output/'placement.gds')
        trace_file.parent.mkdir(parents=True,exist_ok=True)
        trace_file.write_text(json.dumps(trace,indent=2,ensure_ascii=False),encoding='utf-8')
    (output/"result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "area_um2": metrics["measurements"]["material_bbox"]["area_um2"], "verified": metrics["verification"]["passed"]}))


if __name__ == "__main__":
    main()

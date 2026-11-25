"""Cross-PDK, observation-backed structural primitive induction.

This module learns a typed terminal-equality pattern and records support for
relative layout relations. It does NOT pool incompatible PDK areas, learn DRC,
or claim that two same-topology samples identify a size-conditioned optimizer.
Reference geometry lives only in training observations. Exported primitives
contain no instance/net names, physical dimensions, coordinates or sample paths.
"""
from dataclasses import dataclass, asdict
from decimal import Decimal
from itertools import combinations
from pathlib import Path
import hashlib
import re
import klayout.db as db

from clay.pdk.semantic import get_pack, known_packs


#: Experiment id -> semantic pack id. The pack is the single source of layer
#: numbers, cut stacks, label layers, model bindings and CDL unit convention;
#: this adapter only renames roles for its own report vocabulary.
_PACK_ALIASES = {
    "gf180_5v": "gf180mcu_5v",
    "sky130_1v8": "sky130_1v8",
}
_VOLTAGE_CLASS = {"gf180mcu_5v": "5v", "sky130_1v8": "1v8"}


def _adapter_config(pdk_id):
    """Build the observation adapter's view from the semantic rule pack.

    Nothing process-specific is restated here: layer numbers, the cut stack,
    the label layer, model binding, CDL units and extra CDL parameters all come
    from ``Pack``. Adding a PDK is a data change in clay/pdk/semantic.py.
    """
    pack = get_pack(_PACK_ALIASES.get(pdk_id, pdk_id))
    # Start from the pack's canonical roles; the tracer walks exactly these.
    layers = dict(pack.layers)
    # The tracer's own tokens for the two implant and two marker roles.
    layers["nimplant"] = pack.layer("nplus")
    layers["pimplant"] = pack.layer("pplus")
    if "dualgate" in pack.layers:
        layers["oxide"] = pack.layer("dualgate")
    if "v5_xtor" in pack.layers:
        layers["v5"] = pack.layer("v5_xtor")

    # Cut stacks are already expressed in canonical roles, so no translation is
    # needed beyond confirming the tracer can address every named conductor.
    cuts = {role: tuple(conductors) for role, conductors in pack.cut_stack.items()}
    for conductors in cuts.values():
        for conductor in conductors:
            if conductor != "sd" and conductor not in layers:
                raise ValueError(f"{pack.pdk_id}: cut stack names unknown conductor {conductor!r}")

    # Net labels sit on their own text layers, declared per conductor role.
    labels = dict(pack.annotation_layers)
    for role in labels:
        if role not in layers:
            raise ValueError(f"{pack.pdk_id}: label role {role!r} has no drawing layer")
    return {
        "pack": pack,
        "models": dict(pack.model_polarity),
        "unit": pack.cdl_units,
        "voltage_class": _VOLTAGE_CLASS.get(pack.pdk_id, pack.pdk_id),
        "layers": layers,
        "cuts": cuts,
        "labels": labels,
        "extras": set(pack.cdl_extra_params),
        "device_markers": tuple(pack.device_markers),
        "model_markers": dict(pack.model_markers),
    }


class _AdapterConfigs:
    """Lazy mapping: importing this module must not parse a DRC deck."""

    def __contains__(self, key):
        return key in _PACK_ALIASES or key in known_packs()

    def __getitem__(self, key):
        if key not in _PACK_ALIASES and key not in known_packs():
            raise KeyError(key)
        return _adapter_config(key)

    def keys(self):
        return list(_PACK_ALIASES)

    def __iter__(self):
        return iter(self.keys())


PDKS = _AdapterConfigs()
TERMINALS = ("drain", "gate", "source", "body")
FIELDS = tuple(f"{role}.{terminal}" for role in ("n", "p") for terminal in TERMINALS)
RELATIONS = ("active_left", "active_right", "active_center_x", "gate_left", "gate_right",
             "gate_center_x", "parallel_gate_axes", "p_above_n", "separate_active")
SCHEMA = "clay.structural-primitive.v3"


@dataclass(frozen=True)
class TrainingMos:
    name: str
    model: str
    kind: str
    drain: str
    gate: str
    source: str
    body: str
    w_nm: int
    l_nm: int
    m: int
    nf: int
    attributes: tuple


@dataclass(frozen=True)
class TrainingCircuit:
    name: str
    ports: tuple
    devices: tuple
    pdk: str


def parse_training_cdl(text, pdk):
    """Explicit source-adapter units; never guess units from magnitude.

    The SkyWater SPICE export uses X wrappers and a different scale convention;
    this adapter intentionally accepts the accompanying MOS CDL only. Geometry
    must independently confirm every converted W/L before training admission.
    """
    config = PDKS[pdk]
    lines = []
    for raw in text.splitlines():
        line = raw.split("$",1)[0].strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if not lines: raise ValueError("Orphan continuation")
            lines[-1] += " "+line[1:]
        else:
            lines.append(line)
    if not lines or not lines[0].lower().startswith(".subckt ") or not lines[-1].lower().startswith(".ends"):
        raise ValueError("Expected exactly one complete CDL subcircuit")
    header=lines[0].split();name=header[1];ports=tuple(header[2:])
    end=lines[-1].split()
    if len(end)>1 and end[1] != name: raise ValueError("Mismatched ENDS")
    if len(set(ports)) != len(ports): raise ValueError("Duplicate ports")
    devices=[]
    def length(value):
        # GF180 CDL appears both as SI bare literals and as explicit U suffixes
        # across its published cell libraries. Both are unambiguous; SKY keeps
        # its documented bare micrometre convention in this adapter.
        match = re.fullmatch(r"[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?([munp]?)", value, re.I)
        if not match:
            raise ValueError("Unsupported CDL dimension expression")
        suffix = match.group(1).lower()
        if config["unit"] == "metre":
            if suffix not in {"", "u", "n", "m", "p"}:
                raise ValueError("Unsupported GF180 dimension suffix")
            scale = {"": Decimal("1e9"), "m": Decimal("1e6"), "u": Decimal("1e3"),
                     "n": Decimal("1"), "p": Decimal(".001")} [suffix]
        else:
            if suffix:
                raise ValueError("SKY130 CDL dimensions must use documented micrometre convention")
            scale = Decimal(1000)
        v = Decimal(value[:-len(suffix)] if suffix else value) * scale
        if v<=0 or v!=int(v) or int(v)%5: raise ValueError("Off-grid or invalid physical size")
        return int(v)
    for line in lines[1:-1]:
        t=line.split()
        if len(t)<8 or not t[0].lower().startswith("m"): raise ValueError("Expected MOS CDL, not SPICE X wrapper or nested subcircuit")
        model=t[5].lower()
        if model not in config["models"]: raise ValueError("Model/PDK mismatch")
        pairs=re.findall(r"(\w+)\s*=\s*([^\s=]+)"," ".join(t[6:]))
        residue=re.sub(r"\w+\s*=\s*[^\s=]+",""," ".join(t[6:])).strip()
        params={k.lower():v for k,v in pairs}
        allowed={"w","l","m","mult","nf"}|config["extras"]
        if residue or len(params)!=len(pairs) or params.keys()-allowed or not {"w","l"}<=params.keys():
            raise ValueError("Unknown or duplicate CDL parameters")
        # Multiplicity conventions are not silently combined. Only unity is
        # supported in these two training cells; retain all original annotations.
        for key in ("m","mult","nf"):
            if Decimal(params.get(key,"1")) != 1: raise ValueError("Only unity multiplicity/finger annotations supported")
        devices.append(TrainingMos(t[0],model,config["models"][model],*t[1:5],length(params["w"]),length(params["l"]),1,1,tuple(sorted(params.items()))))
    if len(devices)!=2 or len({d.name for d in devices})!=2 or {d.kind for d in devices}!={"nmos","pmos"}:
        raise ValueError("Observation scope requires one complementary MOS pair")
    return TrainingCircuit(name,ports,tuple(devices),pdk)


def roles(circuit):
    ns=[d for d in circuit.devices if d.kind=="nmos"]
    ps=[d for d in circuit.devices if d.kind=="pmos"]
    if len(ns)!=1 or len(ps)!=1 or len(circuit.devices)!=2: raise ValueError("One n and one p role required")
    return {"n":ns[0],"p":ps[0]}


def connectivity_signature(circuit):
    binding=roles(circuit)
    nets={field:getattr(binding[field.split(".")[0]],field.split(".")[1]) for field in FIELDS}
    partition=[]
    for net in dict.fromkeys(nets.values()):
        partition.append([field for field in FIELDS if nets[field]==net])
    return partition


def observe_sample(gds_path, circuit):
    config=PDKS[circuit.pdk]
    layout=db.Layout();layout.read(str(gds_path));top=layout.top_cell()
    if len(layout.top_cells())!=1 or layout.dbu!=.001 or any(True for _ in top.each_inst()):
        raise ValueError("Training adapter requires flat 1nm GDS and a single top")
    regs={k:db.Region(top.begin_shapes_rec(layout.layer(*layer))).merged() for k,layer in config["layers"].items()}
    nodes=[]
    conductor_names={k for values in config["cuts"].values() for k in values}
    for key in sorted(conductor_names|config["cuts"].keys()):
        reg=regs["active"]-regs["poly"] if key=="sd" else regs[key]
        nodes.extend((key,db.Region(p)) for p in reg.each())
    parent=list(range(len(nodes)))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,(kind,reg) in enumerate(nodes):
        if kind not in config["cuts"]: continue
        for j,(other,r) in enumerate(nodes):
            if other in config["cuts"][kind] and not (reg&r).is_empty(): parent[root(i)]=root(j)
    labels={}
    for conductor,key in config["labels"].items():
        for shape in top.shapes(layout.layer(*key)).each():
            if not shape.is_text(): continue
            text=shape.text;point=db.Point(text.x,text.y)
            for i,(kind,reg) in enumerate(nodes):
                if kind==conductor and any(p.inside(point) for p in reg.each()): labels.setdefault(root(i),set()).add(text.string)
    def traced(region,kind):
        return set().union(*(labels.get(root(i),set()) for i,(k,r) in enumerate(nodes) if k==kind and not (region&r).is_empty()))
    def box(b): return [b.left,b.bottom,b.right,b.top]
    bindings={}
    for poly in regs["active"].each():
        own=db.Region(poly);channel=(own&regs["poly"]).merged()
        if channel.is_empty(): continue
        if channel.count()!=1: raise ValueError("Multi-channel active island unsupported")
        ab,cb=own.bbox(),channel.bbox()
        if cb.bottom!=ab.bottom or cb.top!=ab.top: raise ValueError("Source adapter requires vertical gates")
        inside=(own-regs["nwell"]).is_empty()
        if not inside and not (own&regs["nwell"]).is_empty(): raise ValueError("Partial well overlap")
        kind="pmos" if inside else "nmos"
        matches=[d for d in circuit.devices if d.kind==kind and d.w_nm==cb.height() and d.l_nm==cb.width()]
        if len(matches)!=1: raise ValueError("Geometry does not confirm CDL dimensions/model polarity")
        d=matches[0];role="p" if inside else "n"
        if role in bindings: raise ValueError("Ambiguous role binding")
        implant=regs["pimplant" if inside else "nimplant"]
        if not (own-implant).is_empty(): raise ValueError("Implant does not cover active")
        # Every marker the pack requires for this model must cover the device,
        # and every device marker the pack declares must also be present. Both
        # sets are data: GF180's 5 V flavour declares dualgate/v5_xtor as device
        # markers, sky130's HVT PMOS declares hvtp per model. Branching on a
        # process name here made the check invisible for any third process.
        required = set(config["device_markers"]) | set(config["model_markers"].get(d.model, ()))
        for marker in sorted(required):
            if not (own-regs[marker]).is_empty():
                raise ValueError(f"{marker.upper()} marker {marker} missing for {d.model}; "
                                 "the model cannot be silently replaced")
        gate=traced(channel,"poly");sds=traced(own-channel,"sd")
        if gate!={d.gate} or sds!={d.drain,d.source}: raise ValueError("Traced conductor labels disagree with netlist")
        sides={}
        for island in (own-channel).merged().each():
            r=db.Region(island);nets=traced(r,"sd")
            if len(nets)!=1: raise ValueError("Ambiguous S/D labels")
            net=next(iter(nets));terminal="source" if net==d.source else "drain"
            sides[terminal]={"net":net,"bbox_nm":box(r.bbox()),"contacts":(regs["contact"]&r).count()}
        if set(sides)!={"source","drain"}: raise ValueError("Incomplete S/D binding")
        bindings[role]={"device":asdict(d),"active_bbox_nm":box(ab),"channel_bbox_nm":box(cb),
                        "terminals":sides,"gate_nets":sorted(gate),"body_evidence":"CDL + well polarity; external body tap obligation, not local tap extraction"}
    if set(bindings)!={"n","p"}: raise ValueError("Incomplete geometric coverage")
    n,p=bindings["n"]["active_bbox_nm"],bindings["p"]["active_bbox_nm"]
    ng,pg=bindings["n"]["channel_bbox_nm"],bindings["p"]["channel_bbox_nm"]
    observed={"active_left":n[0]==p[0],"active_right":n[2]==p[2],"active_center_x":n[0]+n[2]==p[0]+p[2],
              "gate_left":ng[0]==pg[0],"gate_right":ng[2]==pg[2],"gate_center_x":ng[0]+ng[2]==pg[0]+pg[2],
              "parallel_gate_axes":True,"p_above_n":p[1]>=n[3],"separate_active":(db.Region(db.Box(*n))&db.Region(db.Box(*p))).is_empty()}
    return {"pdk":circuit.pdk,"voltage_class":config["voltage_class"],"cdl_units":config["unit"],
            "parent_sample_id":circuit.pdk+":"+circuit.name,
            "circuit":asdict(circuit),"partition":connectivity_signature(circuit),"bindings":bindings,
            "observed_relations":observed,"gds_sha256":hashlib.sha256(Path(gds_path).read_bytes()).hexdigest(),
            "validation":"exact channel W/L + implants/well/model markers + traced G/S/D labels; not full DRC/LVS",
            "scope":"training-only geometry; never export bindings to runtime library"}


def fit_structural_primitive(observations):
    if not observations: raise ValueError("No training observations")
    partition=observations[0]["partition"]
    if any(o["partition"]!=partition for o in observations):
        raise ValueError("Mixed connectivity signatures require separate primitive families")
    support={rel:sum(o["observed_relations"][rel] for o in observations) for rel in RELATIONS}
    common=[rel for rel in RELATIONS if support[rel]==len(observations)]
    # Sources must be independent raw samples, not duplicated/translated copies.
    if len({o["gds_sha256"] for o in observations})!=len(observations): raise ValueError("Duplicate source observation")
    if len({o["parent_sample_id"] for o in observations})!=len(observations): raise ValueError("Same parent sample cannot count as independent training data")
    library={"schema":SCHEMA,
             "pattern":{"roles":{"n":"nmos","p":"pmos"},"terminal_partition":partition,
                        "distinct_partition_nets":True,"required_port_roles":["n.source","p.source","n.body","p.body"]},
             "program":[{"op":"instantiate_target_mos","role":"n"},
                        {"op":"instantiate_target_mos","role":"p"},
                        {"op":"enumerate_verified_constructions","roles":["n","p"]},
                        {"op":"minimize_validated_area","roles":["n","p"]}],
             "observed_preferences":common,
             "parameter_binding":"target_model_W_L_M_nf_and_terminal_nets",
             "geometry_authority":"target_PDK_only",
             "selection":{"mode":"exhaustive_verified_candidates","learned_size_selector":False}}
    validate_structural_library(library)
    evidence={"sample_count":len(observations),"pdks":sorted({o["pdk"] for o in observations}),
              "relation_support":{r:{"support":support[r],"total":len(observations),"unanimous":r in common} for r in RELATIONS},
              "induced":"typed terminal equality partition and unanimous optional relations",
              "manual_priors":"n/p role alphabet, operator vocabulary, provider legality, exhaustive selection",
              "area_training":"not performed: SKY130 validated generation cost provider is absent; incompatible process areas are not pooled",
              "size_selector":"not identifiable from these two reference cells; no invented split/threshold",
              "source_geometry_exported":False}
    return library,evidence


def validate_structural_library(library):
    expected={"schema","pattern","program","observed_preferences","parameter_binding","geometry_authority","selection"}
    if set(library)!=expected or library["schema"]!=SCHEMA: raise ValueError("Unknown structural library fields/schema")
    p=library["pattern"]
    if set(p)!={"roles","terminal_partition","distinct_partition_nets","required_port_roles"}: raise ValueError("Invalid pattern fields")
    if p["roles"]!={"n":"nmos","p":"pmos"} or p["distinct_partition_nets"] is not True: raise ValueError("Unsupported role pattern")
    if p["required_port_roles"]!=["n.source","p.source","n.body","p.body"]: raise ValueError("Invalid port obligations")
    partition=p["terminal_partition"]
    if not isinstance(partition,list) or any(not isinstance(g,list) or not g for g in partition): raise ValueError("Invalid terminal partition")
    flat=[f for g in partition for f in g]
    if len(flat)!=len(FIELDS) or set(flat)!=set(FIELDS): raise ValueError("Terminal coverage/unknown terminal")
    canonical=[{"op":"instantiate_target_mos","role":"n"},{"op":"instantiate_target_mos","role":"p"},
               {"op":"enumerate_verified_constructions","roles":["n","p"]},{"op":"minimize_validated_area","roles":["n","p"]}]
    if library["program"]!=canonical: raise ValueError("Unverified operator program")
    rels=library["observed_preferences"]
    if not isinstance(rels,list) or len(rels)!=len(set(rels)) or set(rels)-set(RELATIONS): raise ValueError("Invalid relation symbols")
    if library["parameter_binding"]!="target_model_W_L_M_nf_and_terminal_nets" or library["geometry_authority"]!="target_PDK_only": raise ValueError("Invalid parameter binding")
    if library["selection"]!={"mode":"exhaustive_verified_candidates","learned_size_selector":False}: raise ValueError("No trained size selector available")


def match_structural_primitive(library,circuit):
    """Execute the LEARNED partition, not an instance-name/cell-name lookup."""
    validate_structural_library(library)
    binding=roles(circuit)
    actual=connectivity_signature(circuit)
    expected=library["pattern"]["terminal_partition"]
    normalize=lambda partition:{frozenset(group) for group in partition}
    if normalize(actual)!=normalize(expected): raise ValueError("Target terminal equivalences do not match trained primitive")
    for f in library["pattern"]["required_port_roles"]:
        role,terminal=f.split(".")
        if getattr(binding[role],terminal) not in circuit.ports: raise ValueError("Required external body/supply port missing")
    return binding


def execute_structural_primitive(library,circuit,contract,output=None):
    """Lower a matched structural primitive using a verified construction provider.

    Reject an absent generation provider instead of fabricating a cross-PDK GDS.
    The structural pattern and evidence are reusable across both input adapters.

    The gate is a declared capability, not a process name: asserting
    ``pdk == 'gf180mcu_5v'`` hard-codes the answer and would keep excluding a
    process that gains a provider later.
    """
    match_structural_primitive(library,circuit)
    pack = get_pack(_PACK_ALIASES.get(circuit.pdk, circuit.pdk))
    if "structural_lowering" not in pack.capability:
        raise ValueError(f"pack {pack.pdk_id!r} declares no 'structural_lowering' "
                         "capability; its structural primitive is observation-only "
                         "and no construction-cost provider is connected")
    from clay.learning.circuit import CircuitGraph,Mos
    from clay.learning.construction_search import search_constructions,make_policy
    target=CircuitGraph(circuit.name,circuit.ports,tuple(Mos(d.name,d.model,d.drain,d.gate,d.source,d.body,d.w_nm,d.l_nm,d.m,d.nf) for d in circuit.devices))
    result=search_constructions(target,contract,make_policy(),budget=16,output=output)
    result["structural_primitive"]={"schema":SCHEMA,"matched_terminal_partition":library["pattern"]["terminal_partition"],
                                    "style_constraints_applied":False,"size_selector_learned":False,
                                    "lowering":"existing verified GF180 constructors + exhaustive search; no learning area benefit claimed"}
    return result

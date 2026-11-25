"""Evaluation/training-only geometry extraction, including shared diffusion.

Layer roles come from the semantic rule pack, so the same extractor reads a
GF180 GDS and a SKY130 GDS without knowing either process.
"""
from collections import Counter
from dataclasses import asdict
import hashlib
from pathlib import Path
import klayout.db as db
from clay.pdk.semantic import default_pack


def box(b):
    return [b.left, b.bottom, b.right, b.top]


def regions(layout, *, pack=None):
    p = default_pack() if pack is None else pack
    top = layout.top_cell()
    return {k: db.Region(top.begin_shapes_rec(layout.layer(*p.layer(k)))).merged()
            for k in p.layers}


def trace(layout, *, pack=None):
    """Trace diffusion/poly through every cut level to labelled conductors.

    The stack depth is a property of the process, not of this function. GF180
    has one cut level (contact: sd/poly -> metal1); sky130 has two
    (licon: sd/poly -> li, then mcon: li -> met1). Walking ``pack.cut_stack``
    instead of a hard-coded three-role list is what lets the same tracer read
    both: with only the first level, sky130's VGND/VPWR labels sit on metal2
    and every supply-side diffusion segment comes back unlabelled.
    """
    p = default_pack() if pack is None else pack
    regs = regions(layout, pack=p)
    if not p.cut_stack:
        raise ValueError(f"pack {p.pdk_id!r} declares no cut stack; connectivity is unknown")
    # Conductors are the roles the cut stack names, plus the diffusion node that
    # the first level lands on. Only roles this pack actually draws are used.
    conductors = {'sd'}
    for _cut, roles in p.cut_stack.items():
        conductors.update(r for r in roles if r != 'sd')
    conductors &= ({'sd'} | set(p.layers))
    cuts = [c for c in p.cut_stack if c in p.layers]
    if not cuts:
        raise ValueError(f"pack {p.pdk_id!r} cut roles {sorted(p.cut_stack)} have no layers")

    # A butting body tap can share an active polygon with transistor diffusion.
    # Split at the N+/P+ boundary: opposite implants are not an ohmic conductor.
    nodes = [('sd', db.Region(q)) for implant in ['nplus', 'pplus']
             for q in ((regs['active'] & regs[implant]) - regs['poly']).merged().each()]
    nodes += [(k, db.Region(q)) for k in sorted(conductors - {'sd'}) for q in regs[k].each()]
    nodes += [(k, db.Region(q)) for k in cuts for q in regs[k].each()]
    parent = list(range(len(nodes)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # A cut joins exactly the conductors its stack entry names.
    for i, (k, r) in enumerate(nodes):
        if k not in cuts:
            continue
        for j, (other, s) in enumerate(nodes):
            if other in p.cut_stack[k] and not (r & s).is_empty():
                parent[root(i)] = root(j)
    labels = {}
    # The text layer is NOT derivable from the drawing layer: GF180 labels metal1
    # at 34/10 over 34/0, sky130 labels li at 67/5 over 67/20 and met1 at 68/5.
    # It is declared per conductor role in Pack.annotation_layers, so read it
    # from there instead of hard-coding one process's numbers.
    for role, layer_xy in p.annotation_layers.items():
        if role not in conductors:
            continue
        for shape in layout.top_cell().shapes(layout.layer(*layer_xy)).each():
            if not shape.is_text():
                continue
            t = shape.text
            for i, (k, r) in enumerate(nodes):
                if k == role and any(q.inside(db.Point(t.x, t.y)) for q in r.each()):
                    labels.setdefault(root(i), set()).add(t.string)

    def nets(region, kind):
        return sorted(set().union(*(labels.get(root(i), set())
                                    for i, (k, r) in enumerate(nodes)
                                    if k == kind and not (region & r).is_empty())))
    return regs, nets


def extract(layout,circuit,contract,*,pack=None):
    p=default_pack() if pack is None else pack
    if layout.dbu!=.001 or len(layout.top_cells())!=1:
        raise ValueError('Expected one top and 1 nm DBU')
    regs,nets=trace(layout,pack=p)
    # Only the marker roles this process actually declares are checked; a pack
    # with no oxide/voltage marker must not demand evidence for one.
    markers=[m for m in ('dualgate','v5_xtor') if m in regs]
    islands=[];found=[]
    transistor_polygons=[q for implant in ('nplus','pplus') if implant in regs
                         for q in (regs['active']&regs[implant]).merged().each()]
    for polygon in transistor_polygons:
        own=db.Region(polygon);channels=(own&regs['poly']).merged()
        if channels.is_empty(): continue  # Body taps are not MOS channels.
        a=own.bbox();p_kind=(own-regs['nwell']).is_empty();kind='pmos' if p_kind else 'nmos'
        if not p_kind and not (own&regs['nwell']).is_empty(): raise ValueError('Partial well overlap')
        if any(not (own-regs[k]).is_empty() for k in [('pplus' if p_kind else 'nplus')]+markers):
            raise ValueError('Missing polarity/voltage evidence')
        c=next(channels.each()).bbox()
        vertical=c.bottom==a.bottom and c.top==a.top
        horizontal=c.left==a.left and c.right==a.right
        if vertical==horizontal: raise ValueError('Unsupported channel orientation')
        order=lambda q:q.bbox().left if vertical else q.bbox().bottom
        gates=sorted(channels.each(),key=order)
        segments=[]
        for q in sorted((own-channels).merged().each(),key=order):
            r=db.Region(q);net=nets(r,'sd')
            if len(net)!=1: raise ValueError('Ambiguous/unlabelled SD network')
            cuts=regs['contact'].inside(r)
            segments.append({'net':net[0],'bbox_nm':box(q.bbox()),'contact_count':cuts.count(),
                'contact_centers_local_nm':sorted([[c.bbox().center().x-a.left,c.bbox().center().y-a.bottom] for c in cuts.each()])})
        if len(segments)!=len(gates)+1: raise ValueError('Not a simple diffusion chain')
        gate_nets=[]
        for i,g in enumerate(gates):
            b=g.bbox();gn=nets(db.Region(g),'poly')
            if len(gn)!=1: raise ValueError('Ambiguous/unlabelled gate')
            gate_nets.append(gn[0])
            w,l=(b.height(),b.width()) if vertical else (b.width(),b.height())
            if (vertical and (b.bottom!=a.bottom or b.top!=a.top)) or (horizontal and (b.left!=a.left or b.right!=a.right)):
                raise ValueError('Channel does not span active')
            body=contract.p_body if p_kind else contract.n_body
            found.append((kind,w,l,gn[0],tuple(sorted([segments[i]['net'],segments[i+1]['net']])),body))
        islands.append({'kind':kind,'active_bbox_nm':box(a),'active_is_rectangle':own.area()==a.area(),'vertical_gates':vertical,'gate_count':len(gates),
            'channel_bboxes_nm':[box(g.bbox()) for g in gates],'gate_nets':gate_nets,'diffusion_segments':segments})
    expected=[(d.kind,d.w_nm,d.l_nm,d.gate,tuple(sorted([d.source,d.drain])),d.body) for d in circuit.devices]
    if Counter(expected)!=Counter(found): raise ValueError('GDS channels/nets differ from target CDL')
    return {'islands':sorted(islands,key=lambda d:d['kind']),'channel_count':len(found),
            'circuit':asdict(circuit),'validation':'exact W/L and labelled G/SD connectivity; unordered S/D; body from contract/well; not full LVS'}


def observe(path,circuit,contract,*,pack=None):
    layout=db.Layout();layout.read(str(path))
    out=extract(layout,circuit,contract,pack=pack)
    out['gds_sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    out['sample_id']=circuit.name
    return out

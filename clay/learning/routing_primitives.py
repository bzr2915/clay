"""Training-derived Poly/M1 routing preferences for the inverter experiment.

Reusable operator vocabulary and exact orthogonal path search are engineering
choices. Learned: supported input join layer, per-role bottleneck width and
routing-axis preference. Connectivity is mandatory, not a learned primitive.
No reference geometry is accessible during inference.
"""
from collections import defaultdict
from dataclasses import asdict
import heapq
from math import ceil,isfinite
from statistics import mean
import klayout.db as db

from clay.learning import placement_relations as placement
from clay.learning import contact_primitives as contacts
from clay.learning.contact_observations import extract,trace,regions
from clay.learning.generate import measure_layout
from clay.pdk.semantic import default_pack

SCHEMA='clay.routing-primitive.v2'


def _pack(pack):
    return default_pack() if pack is None else pack


def role_nets(circuit,contract,*,pack=None):
    _,gate,out=contacts.inverter_groups(circuit,contract,pack=_pack(pack))
    return {'input':gate,'output':out,'high':contract.high,'low':contract.low}


def minimum_width(region):
    """Measured bottleneck, not bounding-box width; integer 5 nm lattice."""
    if region.is_empty(): raise ValueError('Empty training conductor')
    lo,hi=0,max(region.bbox().width(),region.bbox().height())//5+1
    while hi-lo>1:
        mid=(lo+hi)//2
        if region.width_check(mid*5).is_empty(): lo=mid
        else: hi=mid
    return lo*5


def observe(layout,circuit,contract,*,pack=None):
    p=_pack(pack)
    observation=extract(layout,circuit,contract);r,nets=trace(layout)
    islands=observation['islands']
    if not all(i['vertical_gates']==islands[0]['vertical_gates'] for i in islands):
        raise ValueError('Training requires parallel gate axes')
    vertical=islands[0]['vertical_gates'];result={}
    graph=physical_components(layout)
    if graph['shorted_components'] or any(graph['components_by_net'].get(net)!=1 for net in role_nets(circuit,contract).values()):
        raise ValueError('Disconnected/shorted training nets')
    for role,net in role_nets(circuit,contract).items():
        candidates=[]
        # Operator discovery vocabulary: direct shared Poly if evidenced, else
        # a common M1 join. Preference for the direct conductor is an authored
        # abstraction rule; the selected layer is measured, not forced in fit.
        for layer in (['poly','metal1'] if role=='input' else ['metal1']):
            conductor=db.Region()
            for q in r[layer].each():
                if nets(db.Region(q),layer)==[net]: conductor.insert(q)
            conductor=conductor.merged()
            if conductor.count()!=1: continue
            if layer=='poly':
                channels=db.Region()
                for i in islands:
                    for box in i['channel_bboxes_nm']: channels.insert(db.Box(*box))
                if not (channels-conductor).is_empty(): continue
            candidates.append((layer,conductor))
        if not candidates: raise ValueError(f'No supported common conductor for training {role}')
        layer,conductor=candidates[0]
        along=total=0
        for q in conductor.each():
            for edge in q.each_edge():
                dx=abs(edge.p2.x-edge.p1.x);dy=abs(edge.p2.y-edge.p1.y)
                if dx and dy: raise ValueError('Only orthogonal routing training is supported')
                along+=dy if vertical else dx;total+=dx+dy
        lower=p.nm('poly_width') if layer=='poly' else p.nm('metal_width')
        width=minimum_width(conductor)
        if width<lower: raise ValueError('Observed conductor below supported width rule')
        result[role]={'layer':layer,'width_excess_ratio':(width-lower)/lower,
                      'along_gate_edge_fraction':along/total}
    return result


def fit(placement_library,examples,*,pack=None):
    p=_pack(pack)
    placement.validate_library(placement_library,pack=p)
    if len(examples)<2 or len({e['sample_id'] for e in examples})!=len(examples) or len({e['gds_sha256'] for e in examples})!=len(examples):
        raise ValueError('Need distinct training parents')
    policies={}
    for role in ['input','output','high','low']:
        xs=[e['policies'][role] for e in examples]
        if len({x['layer'] for x in xs})!=1: raise ValueError('Mixed routing layer patterns need richer model')
        policies[role]={'layer':xs[0]['layer'],
                        'width_excess_ratio':mean(x['width_excess_ratio'] for x in xs),
                        'along_gate_edge_fraction':mean(x['along_gate_edge_fraction'] for x in xs),
                        'parents':len(xs)}
    lib={'schema':SCHEMA,'pdk':p.pdk_id,'placement':placement_library,'routing':policies,
         'operator':'poly_gate_join_and_m1_net_tree_v1'}
    validate_library(lib,pack=p)
    return lib,{'examples':examples,'fit':'equal-parent means of bottleneck-width excess and perimeter-axis fraction',
               'manual':'role vocabulary, candidate layers and direct-conductor abstraction priority, path search, finite direction cost, routing order',
               'limitations':'two Poly/M1 sources; no learned vias/multilayer, external taps, no electrical width guarantee'}


def validate_library(lib,*,pack=None):
    p=_pack(pack)
    if set(lib)!={'schema','pdk','placement','routing','operator'} or lib['schema']!=SCHEMA or lib['pdk']!=p.pdk_id or lib['operator']!='poly_gate_join_and_m1_net_tree_v1':
        raise ValueError('Unknown routing library or mismatched rule pack')
    placement.validate_library(lib['placement'],pack=p)
    if set(lib['routing'])!={'input','output','high','low'}: raise ValueError('Missing route roles')
    for role,p in lib['routing'].items():
        if set(p)!={'layer','width_excess_ratio','along_gate_edge_fraction','parents'}:
            raise ValueError('Unknown routing policy fields')
        if p['layer'] not in (['poly','metal1'] if role=='input' else ['metal1']): raise ValueError('Unsupported learned layer')
        if type(p['parents'])!=int or p['parents']<2: raise ValueError('Missing route evidence')
        for k in ['width_excess_ratio','along_gate_edge_fraction']:
            if not isinstance(p[k],(float,int)) or not isfinite(p[k]) or p[k]<0: raise ValueError('Invalid routing statistic')
        if p['along_gate_edge_fraction']>1: raise ValueError('Invalid direction fraction')


def segment(a,b,width):
    h=width//2
    return db.Box(min(a[0],b[0])-h,min(a[1],b[1])-h,max(a[0],b[0])+h,max(a[1],b[1])+h)


def orthogonal_path(start,goal,obstacles,width,axis_fraction,vertical,*,pack=None):
    """Exact shape-checked compressed-grid Dijkstra. No training coordinates.

    Production maze is metal-stack only and cannot preserve the observed Poly
    gate backbone. This bounded single-layer adapter uses KLayout region checks
    for all edges; no unchecked snapping or per-cell geometry recipes.
    """
    p=_pack(pack)
    h=width//2;grid=p.nm('grid');xs={start[0],goal[0]};ys={start[1],goal[1]}
    # Obstacles are already expanded for layer clearance. Decomposition keeps
    # concave free channels that a whole-polygon bbox would incorrectly block.
    rects=[p.bbox() for p in obstacles.decompose_trapezoids_to_region().each()]
    for b in rects:
        xs.update([b.left-h-grid,b.right+h+grid]);ys.update([b.bottom-h-grid,b.top+h+grid])
    margin=4*(width+p.nm('metal_space'))
    xs.update([min(xs)-margin,max(xs)+margin]);ys.update([min(ys)-margin,max(ys)+margin])
    xs=sorted(xs);ys=sorted(ys)
    if len(xs)*len(ys)>100000: raise ValueError('Route search budget exceeded')
    source=(xs.index(start[0]),ys.index(start[1]));target=(xs.index(goal[0]),ys.index(goal[1]))
    def point(v): return xs[v[0]],ys[v[1]]
    q=[(0,source)];cost={source:0};prev={};cache={}
    while q:
        dist,node=heapq.heappop(q)
        if dist!=cost[node]: continue
        if node==target:
            path=[node]
            while node!=source: node=prev[node];path.append(node)
            return [point(v) for v in reversed(path)],dist
        for dx,dy in [(1,0),(-1,0),(0,1),(0,-1)]:
            nb=(node[0]+dx,node[1]+dy)
            if not 0<=nb[0]<len(xs) or not 0<=nb[1]<len(ys): continue
            key=tuple(sorted([node,nb]))
            if key not in cache: cache[key]=(db.Region(segment(point(node),point(nb),width))&obstacles).is_empty()
            if not cache[key]: continue
            a,b=point(node),point(nb);length=abs(a[0]-b[0])+abs(a[1]-b[1])
            along=(dy!=0)==vertical
            penalty=(1-axis_fraction) if along else axis_fraction
            nd=dist+length*(1+0.25*penalty)
            if nd<cost.get(nb,float('inf')):
                cost[nb]=nd;prev[nb]=node;heapq.heappush(q,(nd,nb))
    raise ValueError('No legal path within current single-layer routing vocabulary')


def physical_components(layout,*,pack=None):
    """Independent conductor graph: count physical components, never equate labels."""
    p=_pack(pack)
    r=regions(layout,pack=p)
    if not p.cut_stack:
        raise ValueError(f"pack {p.pdk_id!r} declares no cut stack; connectivity is unknown")
    conductors={'sd'}
    for roles in p.cut_stack.values():
        conductors.update(k for k in roles if k!='sd')
    conductors &= ({'sd'} | set(p.layers))
    cuts=[c for c in p.cut_stack if c in p.layers]
    if not cuts:
        raise ValueError(f"pack {p.pdk_id!r} cut roles {sorted(p.cut_stack)} have no layers")
    implants=[i for i in ('nplus','pplus') if i in r]
    nodes=[('sd',db.Region(q)) for implant in implants for q in ((r['active']&r[implant])-r['poly']).merged().each()]
    # Walk every conductor and cut level the pack declares. GF180 has one cut
    # level; sky130 has two, and a hard-coded three-role list leaves the upper
    # level connected to nothing.
    nodes += [(key,db.Region(q)) for key in sorted(conductors-{'sd'}) for q in r[key].each()]
    nodes += [(key,db.Region(q)) for key in cuts for q in r[key].each()]
    parent=list(range(len(nodes)))
    def root(i):
        while parent[i]!=i: parent[i]=parent[parent[i]];i=parent[i]
        return i
    # A cut joins exactly the conductors its stack entry names.
    for i,(key,a) in enumerate(nodes):
        if key not in cuts: continue
        for j,(other,b) in enumerate(nodes):
            if other in p.cut_stack[key] and not (a&b).is_empty(): parent[root(i)]=root(j)
    labels=defaultdict(set);anchor_roots=defaultdict(set)
    # Label layer is pack data: GF180 labels metal1 at 34/10, sky130 at 67/5.
    for role,layer_xy in p.annotation_layers.items():
        if role not in conductors: continue
        for shape in layout.top_cell().shapes(layout.layer(*layer_xy)).each():
            if not shape.is_text(): continue
            t=shape.text;hits=[]
            for i,(key,a) in enumerate(nodes):
                if key==role and any(q.inside(db.Point(t.x,t.y)) for q in a.each()): hits.append(root(i))
            if not hits: raise ValueError('Port label is not on metal')
            for component in hits: labels[component].add(t.string);anchor_roots[t.string].add(component)
    return {'components_by_net':{k:len(v) for k,v in anchor_roots.items()},
            'shorted_components':[sorted(v) for v in labels.values() if len(v)>1],
            'unlabelled_conductor_components':len({root(i) for i in range(len(nodes))}-set(labels))}


def verify_routed(layout,baseline,circuit,contract,plans,*,pack=None):
    p=_pack(pack)
    local=contacts.verify(layout,circuit,contract,plans,routed=True,pack=p)
    a,b=regions(layout,pack=p),regions(baseline,pack=p)
    checks=dict(local['checks'])
    for k in ['active','contact','nwell','nplus','pplus']:
        checks['preserved_'+k]=(a[k]^b[k]).is_empty()
    checks['no_new_channels']=((a['poly']&a['active'])^(b['poly']&b['active'])).is_empty()
    for k in ['poly','metal1']: checks['preserved_access_'+k]=(b[k]-a[k]).is_empty()
    for k in ['dualgate','v5_xtor']:
        checks['preserved_'+k]=(b[k]-a[k]).is_empty()
    checks['added_poly_active_clearance']=((a['poly']-b['poly']) & b['active'].sized(p.nm('field_poly_active') if p.has('field_poly_active') else p.nm('sd_overhang'))).is_empty()
    graph=physical_components(layout,pack=p)
    wanted=set(role_nets(circuit,contract,pack=p).values())
    checks['physical_nets_connected']=set(graph['components_by_net'])==wanted and all(n==1 for n in graph['components_by_net'].values())
    checks['no_physical_shorts']=not graph['shorted_components']
    checks['no_floating_conductors']=graph['unlabelled_conductor_components']==0
    return {'passed':all(checks.values()),'checks':checks,'failed':[k for k,v in checks.items() if not v],
            'check_count':len(checks),'connectivity':graph,
            'scope':'local DRC and physical G/SD/M1/Poly connectivity; body contacts external, not full foundry LVS/DRC/PEX/EM'}


def route_layout(layout,circuit,contract,policies,*,neutral=False,pack=None):
    p=_pack(pack)
    layout=layout.dup();top=layout.top_cell();r,nets=trace(layout,pack=p)
    observed=extract(layout,circuit,contract,pack=p)
    if len({i['vertical_gates'] for i in observed['islands']})!=1:
        raise ValueError('Routing v1 requires parallel gates; no placement retry implemented')
    vertical=observed['islands'][0]['vertical_gates'];routes=[]
    for role,net in role_nets(circuit,contract,pack=p).items():
        pol=policies[role];layer=pol['layer'];r,nets=trace(layout,pack=p)
        own=db.Region();foreign=db.Region()
        for polygon in r[layer].each():
            names=nets(db.Region(polygon),layer)
            if names==[net]: own.insert(polygon)
            else: foreign.insert(polygon)
        lower=p.nm('poly_width') if layer=='poly' else p.nm('metal_width')
        # Keep edges on 5 nm grid: even multiple of the manufacturing grid.
        width=ceil(lower*(1+(0 if neutral else pol['width_excess_ratio']))/10)*10
        fraction=.5 if neutral else pol['along_gate_edge_fraction']
        if own.is_empty(): raise ValueError('No access conductor')
        space=p.nm('poly_space') if layer=='poly' else p.nm('metal_space')
        forbidden=foreign.sized(space)
        if layer=='poly':
            # Existing channels remain immutable; new poly must stay off active.
            forbidden+=r['active'].sized(p.nm('field_poly_active') if p.has('field_poly_active') else p.nm('sd_overhang'))-own
        # Route from contact centers (on owned geometry) and re-evaluate physical
        # components after every branch. Already connected terminals need no wire.
        points=sorted({(q.bbox().center().x,q.bbox().center().y) for q in r['contact'].inside(own).each()})
        if not points: raise ValueError('Missing routing anchors')
        branches=[];length=0
        while own.merged().count()>1:
            polys=list(own.merged().each())
            groups=[[pt for pt in points if q.inside(db.Point(*pt))] for q in polys]
            if any(not g for g in groups): raise ValueError('Unanchored conductor island')
            pairs=sorted((abs(a[0]-b[0])+abs(a[1]-b[1]),a,b) for g in groups[1:] for a in groups[0] for b in g)
            choices=[]
            for _,start,goal in pairs:
                try:
                    path,cost=orthogonal_path(start,goal,forbidden,width,fraction,vertical,pack=p)
                    choices.append((cost,path))
                except ValueError: continue
            if not choices: raise ValueError(f'Unroutable {role} on {layer} at learned width {width}')
            _,path=min(choices,key=lambda x:x[0]);wire=db.Region()
            for a,b in zip(path,path[1:]): wire.insert(segment(a,b,width));length+=abs(a[0]-b[0])+abs(a[1]-b[1])
            old_count=own.merged().count();own=(own+wire).merged()
            if own.count()>=old_count: raise ValueError('Routing branch made no connectivity progress')
            top.shapes(layout.layer(*p.layer(layer))).insert(wire.merged())
            branches.append(path)
        # Close sub-spacing concave notches on this net only. No removal of
        # original accesses, no cross-net bridging or channel modification.
        closed=(own+own.sized(space//2).sized(-space//2)).merged()
        repair=closed-own
        if not (repair&forbidden).is_empty(): raise ValueError('Notch repair intersects a routing obstacle')
        top.shapes(layout.layer(*p.layer(layer))).insert(repair)
        routes.append({'role':role,'net':net,'layer':layer,'width_nm':width,'direction_fraction':fraction,
                       'notch_fill_area_nm2':repair.area(),
                       'branches':branches,'added_centerline_length_nm':length})
    # PL.9/DV enclosure: any newly joined 5 V Poly stays in the same voltage
    # domain. Extend selectors, without modifying active or wells.
    r=regions(layout,pack=p)
    # Marker extension happens only for the marker roles this process declares.
    markers=[m for m in ('dualgate','v5_xtor') if m in r]
    if markers:
        base=db.Region()
        for m in markers: base+=r[m]
        voltage=db.Region((base+r['poly'].sized(p.nm('oxide_over_poly') if p.has('oxide_over_poly') else p.nm('poly_endcap'))).bbox())
        for key in markers:
            top.shapes(layout.layer(*p.layer(key))).insert(voltage)
    top.shapes(layout.layer(*p.boundary_layer)).clear()
    material=db.Region()
    for region in regions(layout,pack=p).values(): material+=region
    top.shapes(layout.layer(*p.boundary_layer)).insert(material.bbox())
    return layout,routes


def generate(lib,circuit,contract,placement_path,routed_path,*,neutral=False,pack=None):
    p=_pack(pack)
    validate_library(lib,pack=p)
    result=placement.generate(lib['placement'],circuit,contract,placement_path,pack=p)
    baseline=db.Layout();baseline.read(str(placement_path))
    routed,routes=route_layout(baseline,circuit,contract,lib['routing'],neutral=neutral,pack=p)
    plans={k:b['contact_plan'] for k,b in result['bindings'].items()}
    verified=verify_routed(routed,baseline,circuit,contract,plans,pack=p)
    if not verified['passed']: raise ValueError('Routed validation failed: '+str(verified['failed']))
    routed.write(str(routed_path));readback=db.Layout();readback.read(str(routed_path))
    verified=verify_routed(readback,baseline,circuit,contract,plans,pack=p)
    if not verified['passed']: raise ValueError('Routed readback failed')
    return {'schema':SCHEMA,'pdk':p.pdk_id,'circuit':asdict(circuit),'placement':result,
            'routes':routes,'verification':verified,'measurements':measure_layout(readback,readback.top_cell(),pack=p),
            'routing_ablation':'neutral widths/direction' if neutral else 'learned widths/direction',
            'reference_inputs':[],'body_taps':'external and not physically generated',
            'learned':'common input join layer; per-net-role bottleneck widths and relative axis preferences',
            'manual':'Poly/M1 operator vocabulary, exact single-layer path search, routing order and direction-cost coefficient'}

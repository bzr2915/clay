"""Learn coordinate-free orientation and net-aware access relationships.

The feature vocabulary and finite ranking weights are engineering choices.
Empirical means are learned; no orientation is forbidden by these preferences.
This reranks area-optimal candidates; it is not a joint placement/routing solver.
"""
from math import isfinite
from statistics import mean
import klayout.db as db

from clay.pdk.semantic import default_pack
from clay.learning import geometry_primitives as geometry
from clay.learning import contact_primitives as contacts
from clay.learning.contact_observations import extract, trace

SCHEMA='clay.placement-relations.v2'
FEATURES=('parallel_gate_axes','separation_along_gates','n_gate_access_inward',
          'p_gate_access_inward','output_transverse_misalignment')
DEFAULT_WEIGHTS={'relations':0.10,'wirelength_proxy':0.05}


def _pack(pack):
    return default_pack() if pack is None else pack


def features(layout,circuit,contract,*,pack=None):
    p=_pack(pack)
    observation=extract(layout,circuit,contract,pack=p)
    regs,nets=trace(layout,pack=p)
    _,gate,output=contacts.inverter_groups(circuit,contract,pack=p)
    islands={i['kind']:i for i in observation['islands']}
    if set(islands)!={'nmos','pmos'} or len(observation['islands'])!=2:
        raise ValueError('Relations require exactly two diffusion islands')
    centers={};axis={};ports={};widths={}
    for kind,island in islands.items():
        a=db.Box(*island['active_bbox_nm'])
        centers[kind]=((a.left+a.right)/2,(a.bottom+a.top)/2)
        axis[kind]=(0,1) if island['vertical_gates'] else (1,0)
        widths[kind]=a.width() if island['vertical_gates'] else a.height()
        own_channels=db.Region()
        for c in island['channel_bboxes_nm']: own_channels.insert(db.Box(*c))
        # A routed input poly component may serve both rows. Its physical
        # contacts are valid access to each connected gate, without inventing
        # per-device contact ownership from an arbitrary nearest-point rule.
        gate_poly=regs['poly'].interacting(own_channels)
        gate_points=[]
        for cut in regs['contact'].inside(gate_poly).each():
            if nets(db.Region(cut),'poly')!=[gate]:
                raise ValueError('Ambiguous gate access net')
            p=cut.bbox().center();gate_points.append((p.x,p.y))
        if not gate_points: raise ValueError('No physical input contact for relation learning')
        out_points=[]
        for seg in island['diffusion_segments']:
            if seg['net']!=output: continue
            pts=seg['contact_centers_local_nm']
            if not pts: raise ValueError('Output has no contact')
            out_points.append((mean(p[0] for p in pts)+a.left,mean(p[1] for p in pts)+a.bottom))
        if not out_points: raise ValueError('No output access')
        ports[kind]={'gate':gate_points,'output':out_points}
    n,p=centers['nmos'],centers['pmos'];delta=(p[0]-n[0],p[1]-n[1])
    norm=delta[0]**2+delta[1]**2
    if norm==0: raise ValueError('Coincident diffusion centers')
    along=mean((delta[0]*v[0]+delta[1]*v[1])**2/norm for v in axis.values())
    f={'parallel_gate_axes':float(axis['nmos']==axis['pmos']), 'separation_along_gates':along}
    for kind,prefix,sign in [('nmos','n',1),('pmos','p',-1)]:
        center=centers[kind]
        f[prefix+'_gate_access_inward']=mean(float(((pt[0]-center[0])*delta[0]+(pt[1]-center[1])*delta[1])*sign>0)
                                                  for pt in ports[kind]['gate'])
    out_centers={k:tuple(mean(pt[i] for pt in v['output']) for i in [0,1]) for k,v in ports.items()}
    od=(out_centers['pmos'][0]-out_centers['nmos'][0],out_centers['pmos'][1]-out_centers['nmos'][1])
    scale=mean(widths.values())
    f['output_transverse_misalignment']=mean(min(1.0,abs(od[0]*v[1]-od[1]*v[0])/scale) for v in axis.values())
    hpwl={}
    for role in ['gate','output']:
        points=set(pt for item in ports.values() for pt in item[role])
        hpwl[role]=max(pt[0] for pt in points)-min(pt[0] for pt in points)+max(pt[1] for pt in points)-min(pt[1] for pt in points)
    return {'features':f,'hpwl_nm':hpwl,'hpwl_total_nm':sum(hpwl.values()),
            'scope':'input contact centers and output landing centroids; no route or RC estimate'}


def fit(geometry_library,examples,*,pack=None):
    p=_pack(pack)
    geometry.validate_library(geometry_library,pack=p)
    if len(examples)<2: raise ValueError('Need at least two independent parent samples')
    if len({e['sample_id'] for e in examples})!=len(examples) or len({e['gds_sha256'] for e in examples})!=len(examples):
        raise ValueError('Duplicate relation training parent')
    for e in examples:
        if set(e['features'])!=set(FEATURES) or any(not isfinite(v) or not 0<=v<=1 for v in e['features'].values()):
            raise ValueError('Invalid relation features')
    policies={key:{'mean':mean(e['features'][key] for e in examples),
                   'variance':mean((e['features'][key]-mean(x['features'][key] for x in examples))**2 for e in examples),
                   'parents':len(examples)} for key in FEATURES}
    lib={'schema':SCHEMA,'pdk':p.pdk_id,'geometry':geometry_library,'relations':policies,
         'operator':'finite_soft_relation_ranking_v1'}
    validate_library(lib,pack=p)
    return lib,{'examples':examples,'fit':'equal-parent empirical feature means; no source coordinates in deployable model',
                'limitations':'two standard-cell parents; correlated styles; no counterexamples or statistical generalization guarantee',
                'manual':'feature definitions, squared-distance loss, ranking weights and candidate search',
                'defaults':DEFAULT_WEIGHTS}


V1_SCHEMA='clay.placement-relations.v1'
#: v1 libraries carried no rule-pack binding and were implicitly fitted for the
#: GF180 5 V experiment. Migration is sound only for that pack.
_V1_PDK='gf180mcu_5v'


def upgrade_library(lib,*,pack=None):
    """Migrate a v1 relation library to the current schema.

    The result records ``migrated_from`` so the schema change stays visible.
    The embedded geometry library is upgraded through its own path.
    """
    p=_pack(pack)
    if lib.get('schema')==SCHEMA:
        return lib
    if lib.get('schema')!=V1_SCHEMA:
        raise ValueError(f'Unknown relation schema/fields: {lib.get("schema")!r}')
    if lib.get('pdk')!=_V1_PDK or p.pdk_id!=_V1_PDK:
        raise ValueError(f'v1 library was fitted for {_V1_PDK!r}; it cannot be migrated to '
                         f'{p.pdk_id!r}. Refit against the target rule pack instead')
    upgraded={k:v for k,v in lib.items() if k!='schema'}
    upgraded['schema']=SCHEMA
    upgraded['geometry']=geometry.upgrade_library(lib['geometry'],pack=p)
    upgraded['migrated_from']=V1_SCHEMA
    return upgraded


def validate_library(lib,*,pack=None):
    p=_pack(pack)
    lib=upgrade_library(lib,pack=p)
    if set(lib)-{'migrated_from'}!={'schema','pdk','geometry','relations','operator'} \
            or lib['schema']!=SCHEMA:
        raise ValueError('Unknown relation schema/fields')
    if lib['pdk']!=p.pdk_id or lib['operator']!='finite_soft_relation_ranking_v1':
        raise ValueError('Unsupported relation operator/PDK')
    geometry.validate_library(lib['geometry'],pack=p)
    if set(lib['relations'])!=set(FEATURES): raise ValueError('Unknown relation features')
    for policy in lib['relations'].values():
        if set(policy)!={'mean','variance','parents'} or type(policy['parents'])!=int or policy['parents']<2:
            raise ValueError('Invalid relation evidence')
        if any(not isinstance(policy[k],(int,float)) or not isfinite(policy[k]) or not 0<=policy[k]<=1
               for k in ['mean','variance']):
            raise ValueError('Invalid relation statistics')


def rank(trials,measurements,lib,weights,*,pack=None):
    validate_library(lib,pack=pack)
    if set(weights)!=set(DEFAULT_WEIGHTS) or any(not isinstance(w,(int,float)) or not isfinite(w) or w<0 for w in weights.values()):
        raise ValueError('Weights must be finite and nonnegative')
    valid=[i for i,t in enumerate(trials) if t['verification']['passed']]
    if not valid: raise ValueError('No legal candidate')
    amin=min(trials[i]['solver']['area_nm2'] for i in valid)
    hmin=max(1,min(measurements[i]['hpwl_total_nm'] for i in valid))
    for i in valid:
        f=measurements[i]['features']
        residuals={k:(f[k]-lib['relations'][k]['mean'])**2 for k in FEATURES}
        loss=mean(residuals.values())
        area=trials[i]['solver']['area_nm2']/amin
        wire=measurements[i]['hpwl_total_nm']/hmin
        trials[i]['ranking']={**measurements[i], 'relation_squared_residuals':residuals,
                              'relation_loss':loss,'area_ratio':area,'wirelength_ratio':wire,
                              'score':area+weights['relations']*loss+weights['wirelength_proxy']*wire}
    return min(valid,key=lambda i:(trials[i]['ranking']['score'],trials[i]['solver']['area_nm2'],i))


def generate(lib,circuit,contract,output,*,weights=None,recorder=None,
             primitive_probe=None,probe_output=None,pack=None):
    p=_pack(pack)
    validate_library(lib,pack=p)
    weights=dict(DEFAULT_WEIGHTS if weights is None else weights)
    geo=lib['geometry']
    def select(trials,layouts):
        measured=[features(l,circuit,contract,pack=p) if t['verification']['passed'] else None for t,l in zip(trials,layouts)]
        selected=rank(trials,measured,lib,weights,pack=p)
        if recorder is not None:
            candidate_scores=[{'candidate':t['candidate'],'passed':t['verification']['passed'],
                               'area_nm2':t['solver']['area_nm2'],
                               'features':None if m is None else m['features'],
                               'hpwl_nm':None if m is None else m['hpwl_nm'],
                               'ranking':t.get('ranking')}
                              for t,m in zip(trials,measured)]
            dependencies=[recorder.candidate_events[t['candidate']]['solver_event'] for t in trials]
            recorder.selection_event=recorder.emit('score_candidates',
                {'candidate_scores':candidate_scores,'target_relations':lib['relations'],
                 'weights':weights,'formula':'area/Amin + relation_weight * mean_squared_relation_error + wire_weight * HPWL/HPWLmin'},
                {'selected_index':selected,'selected_candidate':trials[selected]['candidate']},
                origin='mixed',depends_on=dependencies)
        return selected
    result=contacts._generate(geo['contacts'],circuit,contract,output,
                              geometry_planner=lambda k,c:geometry.geometry_plan(geo,k,c,pack=p),
                              candidate_selector=select,recorder=recorder,
                              primitive_probe=primitive_probe,probe_output=probe_output,pack=p)
    result.update(schema=SCHEMA,pdk=p.pdk_id,
                  learned='geometry/contact model plus relative gate orientation, input access inwardness and output alignment statistics',
                  manual='rectangular constructor, gate landing construction, 16 rotations, area-optimal per-orientation positions; finite scoring weights',
                  scope='soft ranking of 16 legal-search candidates; not global routing-aware optimization or full DRC/LVS/EM',
                  selection_policy={'formula':'area/Amin + relation_weight * mean_squared_relation_error + wire_weight * HPWL/HPWLmin',
                                    'weights':weights,'weight_origin':'engineering defaults, not learned or PDK-mandated',
                                    'rotation_forbidden':False,'coordinate_optimization':'area-first within each orientation, relation-aware reranking across orientations',
                                    'reference_inputs':[]})
    if recorder is not None:
        result['execution_trace_event_ids']={
            'topology':next(e['id'] for e in recorder.document['events'] if e['operation']=='bind_inverter_topology'),
            'selection':recorder.selection_event,
            'materialization':recorder.materialization_ids[result['best']['candidate']]}
    return result

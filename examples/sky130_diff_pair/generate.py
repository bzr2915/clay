"""Portable SKY130 differential-pair placement experiment, not a routed cell."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import klayout.db as db
from clay.layout.device_cell import build_device
from clay.layout.tech import LayoutTech


def generate(output: Path, *, width_um=1.0, length_um=0.5, repeats=1):
    """Build independent PMOS units; retain source/body as distinct declared nets."""
    if not all(math.isfinite(v) and v > 0 for v in (width_um, length_um)):
        raise ValueError('dimensions must be finite and positive')
    if type(repeats) is not int or repeats < 1:
        raise ValueError('repeats must be a positive integer')
    output = Path(output)
    if output.exists():
        raise FileExistsError('use a new output directory')
    tech = LayoutTech.resolve('sky130')
    unit = build_device(tech, 'pmos', width_um * 1e-6, length_um * 1e-6,
                        nets=dict(g='INP', d='OUTP', s='TAIL', b='VDD'))
    if (unit.channel_width_nm, unit.gate_length_nm) != (round(width_um*1000), round(length_um*1000)):
        raise ValueError('requested dimensions were clamped; choose supported dimensions')
    if 'b' not in unit.terminals:
        raise RuntimeError('independent body terminal is unavailable')
    x0,y0,x1,y1=unit.extent()
    # Deliberately noncompact educational placement, not a minimum-spacing claim.
    pitch=2*(x1-x0); row_pitch=2*(y1-y0)
    patterns=[['D']+list('ABBA'*repeats)+['D'], ['D']+list('BAAB'*repeats)+['D']]
    ly=db.Layout();ly.dbu=.001;top=ly.create_cell('SKY130_PAIR_PLACEMENT')
    units=[];centers={'A':[], 'B':[]};off_grid=0
    for row,pattern in enumerate(patterns):
        for column,role in enumerate(pattern):
            nets=dict(g='VDD' if role=='D' else 'INP' if role=='A' else 'INN',
                      d='VDD' if role=='D' else 'OUTP' if role=='A' else 'OUTN',
                      s='VDD' if role=='D' else 'TAIL', b='VDD')
            dev=build_device(tech,'pmos',width_um*1e-6,length_um*1e-6,nets=nets)
            dx,dy=column*pitch-x0,row*row_pitch-y0
            cell=ly.create_cell(f'UNIT_{row}_{column}')
            for poly in dev.polygons:
                pts=[db.Point(p.x,p.y) for p in poly.polygon.vertices]
                off_grid+=sum(p.x%5!=0 or p.y%5!=0 for p in pts)
                cell.shapes(ly.layer(poly.layer,poly.datatype)).insert(db.Polygon(pts))
            top.insert(db.CellInstArray(cell.cell_index(),db.Trans(dx,dy)))
            channels=dev.comp_bbox;cx=dx+(channels[0]+channels[2])/2;cy=dy+(channels[1]+channels[3])/2
            if role!='D':centers[role].append((cx,cy))
            units.append(dict(row=row,column=column,role=role,nets=nets,
                ports={k:dict(net=v.net,bbox=[v.bbox[0]+dx,v.bbox[1]+dy,v.bbox[2]+dx,v.bbox[3]+dy]) for k,v in dev.terminals.items()}))
    mean=lambda name:[sum(p[k] for p in centers[name])/len(centers[name]) for k in (0,1)]
    if mean('A') != mean('B'):
        raise ValueError('declared placement centroids differ')
    output.mkdir(parents=True);ly.write(str(output/'placement.gds'))
    measured = measure_channels(output/'placement.gds', units)
    if any(c['width_nm'] != round(width_um*1000) or c['length_nm'] != round(length_um*1000) for c in measured):
        raise ValueError('read-back channel dimensions differ from requested dimensions')
    report=dict(measured_channels=measured, scope='placement and declared terminal metadata only',pdk='sky130',
        requested_width_um=width_um,requested_length_um=length_um,units=units,
        active_units_per_side=len(centers['A']),dummy_units=4,
        active_center_nm=mean('A'),centroid='PASS_PLACEMENT_METADATA',
        off_grid_vertices=off_grid,manufacturing_grid='FAIL' if off_grid else 'PASS_GEOMETRY_SCREEN',
        routing='NOT_IMPLEMENTED',shared_diffusion='NOT_IMPLEMENTED',common_well_ring='NOT_IMPLEMENTED',
        drc='NOT_RUN',lvs='NOT_RUN',pex='NOT_RUN',simulation='NOT_RUN')
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def measure_channels(gds_path, units):
    """Read actual poly/active intersections per placed instance, not labels."""
    ly=db.Layout();ly.read(str(gds_path)); top=ly.top_cell()
    # Public SKY130 drawing layers, not label/pin layers.
    active=ly.find_layer(65,20);poly=ly.find_layer(66,20)
    if active is None or poly is None:
        raise ValueError('missing SKY130 active/poly drawing layers')
    instances=list(top.each_inst());by_name={ly.cell(i.cell_index).name:i for i in instances}
    result=[];centers={'A':[], 'B':[]}
    for unit in units:
        name=f"UNIT_{unit['row']}_{unit['column']}"
        inst=by_name[name];cell=ly.cell(inst.cell_index)
        channels=db.Region(cell.begin_shapes_rec(active)) & db.Region(cell.begin_shapes_rec(poly))
        polygons=list(channels.each())
        if len(polygons)!=1:
            raise ValueError('expected exactly one channel per independent unit')
        box=polygons[0].transformed(inst.trans).bbox()
        center=[(box.left+box.right)/2,(box.bottom+box.top)/2]
        result.append(dict(cell=name,role=unit['role'],width_nm=box.height(),length_nm=box.width(),center_nm=center))
        if unit['role']!='D':centers[unit['role']].append(center)
    mean=lambda name:[sum(p[k] for p in centers[name])/len(centers[name]) for k in (0,1)]
    if mean('A')!=mean('B'):
        raise ValueError('measured channel centroids differ')
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--width-um',type=float,default=1.0);ap.add_argument('--length-um',type=float,default=.5)
    ap.add_argument('--repeats',type=int,default=1);a=ap.parse_args()
    result=generate(a.output,width_um=a.width_um,length_um=a.length_um,repeats=a.repeats)
    print(json.dumps({k:v for k,v in result.items() if k!='units'},indent=2))

import importlib.util
from pathlib import Path
import pytest
import klayout.db as db
spec=importlib.util.spec_from_file_location('pair_example',Path(__file__).parents[1]/'examples/sky130_diff_pair/generate.py')
example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)

@pytest.mark.parametrize('width,repeats',[(1.0,1),(2.0,2)])
def test_roundtrip_and_declared_connections(tmp_path,width,repeats):
    out=tmp_path/'new';r=example.generate(out,width_um=width,repeats=repeats)
    layout=db.Layout();layout.read(str(out/'placement.gds'))
    assert layout.top_cell().child_instances()==len(r['units'])
    for u in r['units']:
        assert u['ports']['b']['net']=='VDD'
        assert u['ports']['s']['net']==('VDD' if u['role']=='D' else 'TAIL')
    assert r['active_units_per_side']==4*repeats
    assert r['dummy_units']==4
    assert len(r['measured_channels'])==len(r['units'])
    assert all(c['width_nm']==round(width*1000) and c['length_nm']==500 for c in r['measured_channels'])
    assert r['routing']=='NOT_IMPLEMENTED' and r['lvs']=='NOT_RUN'
    with pytest.raises(FileExistsError):example.generate(out)

@pytest.mark.parametrize('w',[0,-1,float('nan'),float('inf')])
def test_invalid_dimensions(tmp_path,w):
    with pytest.raises(ValueError):example.generate(tmp_path/'new',width_um=w)


def test_channel_measurement_rejects_shifted_active_unit(tmp_path):
    out=tmp_path/'new';r=example.generate(out)
    layout=db.Layout();layout.read(str(out/'placement.gds'))
    top=layout.top_cell()
    for inst in top.each_inst():
        if layout.cell(inst.cell_index).name=='UNIT_0_1':
            inst.trans=db.Trans(inst.trans.disp.x+100,inst.trans.disp.y)
            break
    layout.write(str(out/'shifted.gds'))
    with pytest.raises(ValueError,match='centroids differ'):
        example.measure_channels(out/'shifted.gds',r['units'])

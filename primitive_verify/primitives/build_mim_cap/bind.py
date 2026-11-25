"""build_mim_cap 提交：MIM 电容（capm/met3/via3/met4）。"""
from clay.learning import contact_primitives as cp
from clay.learning import passive_primitives as pp
from clay.learning.netlist import Device
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    dev = Device(p['name'], 'cap_mim', p['model'], p['nets'], p['w_nm'], p['l_nm'])
    row = ctx.call(pp.build_mim_cap, dev, pack=pack)
    layout = ctx.call(cp.materialize, {'r': row}, {'r': [0, 0]}, pack=pack, merge='close')
    return {'row': ctx.raw(row), 'layout': ctx.layout(layout)}

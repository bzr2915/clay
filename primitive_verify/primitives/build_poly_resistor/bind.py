"""build_poly_resistor 提交：高阻 poly 电阻（头部风格来自训练集）。"""
from clay.learning import contact_primitives as cp
from clay.learning import passive_primitives as pp
from clay.learning.netlist import Device
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    dev = Device(p['name'], 'res_poly', p['model'], p['nets'], p['w_nm'], p['l_nm'])
    row = ctx.call(pp.build_poly_resistor, dev, p['style'], pack=pack)
    layout = ctx.call(cp.materialize, {'r': row}, {'r': [0, 0]}, pack=pack, merge='close')
    return {'row': ctx.raw(row), 'layout': ctx.layout(layout)}

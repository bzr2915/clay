"""build_chain 提交：三沟道、三个不同栅网的共享扩散链（build_row 通用形式）。"""
from clay.learning import contact_primitives as cp
from clay.learning.circuit import Mos
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    devs = [Mos(**p['devices'][0]), Mos(**p['devices'][1]), Mos(**p['devices'][2])]
    row = ctx.call(cp.build_row, devs, None, p['plan'], None, None, None, pack=pack,
                   gate_nets=p['gate_nets'], segment_nets=p['segment_nets'], kind='nmos')
    layout = ctx.call(cp.materialize, {'r': row}, {'r': [0, 0]}, pack=pack, merge='close')
    return {'row': ctx.raw(row), 'layout': ctx.layout(layout)}

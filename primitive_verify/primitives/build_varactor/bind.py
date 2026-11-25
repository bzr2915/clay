"""build_varactor 提交：MOS 变容管 = n 阱内 n+ tap 上的 poly 沟道（build_row 的变容管配置）。"""
from clay.learning import contact_primitives as cp
from clay.learning.circuit import Mos
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    dev = Mos(**p['device'])
    row = ctx.call(cp.build_row, [dev], None, p['plan'], None, None, None, pack=pack,
                   gate_nets=[p['top']], segment_nets=[p['bot'], p['bot']], kind='nmos',
                   diffusion_role='tap', well=True, rule_overrides=p['rule_map'])
    layout = ctx.call(cp.materialize, {'r': row}, {'r': [0, 0]}, pack=pack, merge='close')
    return {'row': ctx.raw(row), 'layout': ctx.layout(layout)}

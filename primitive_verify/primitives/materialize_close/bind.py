"""materialize_close 提交：按各层间距规则闭运算合并注入层（多行、同/异极性）。"""
from clay.learning import contact_primitives as cp
from clay.pdk.semantic import get_pack
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    pack = get_pack(p['pdk'])
    rows = {'N1': fixtures.row(p['n_row'], [], 'nmos'), 'N2': fixtures.row(p['n_row'], [], 'nmos'),
            'N3': fixtures.row(p['n_row'], [], 'nmos'), 'P1': fixtures.row(p['p_row'], [], 'pmos')}
    layout = ctx.call(cp.materialize, rows, p['origins'], pack=pack, merge='close')
    return {'layout': ctx.layout(layout)}

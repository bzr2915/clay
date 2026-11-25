"""reflect 提交：左右镜像并改名（匹配对的另一半），以及上下镜像。"""
from clay.learning import contact_primitives as cp
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    row = fixtures.row(p['row'], p['terminals'])
    m = ctx.call(cp.reflect, row, 'y', p['net_map'])
    mx = ctx.call(cp.reflect, row, 'x')
    layout = ctx.call(cp.materialize, {'r': m}, {'r': [0, 0]})
    return {'m': ctx.raw(m), 'mx': ctx.raw(mx), 'layout': ctx.layout(layout)}

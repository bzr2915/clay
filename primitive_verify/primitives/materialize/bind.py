"""materialize 提交：两行按原点组装，观察同层注入合并。"""
from clay.learning import contact_primitives as contacts
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    rows = {'R1': fixtures.row(p['row'], p['terminals']),
            'R2': fixtures.row(p['row'], p['terminals'])}
    layout = ctx.call(contacts.materialize, rows, {'R1': (0, 0), 'R2': (0, p['dy'])})
    return {'layout': ctx.layout(layout)}

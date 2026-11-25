"""build_row 提交：只组装输入并调用工程函数。"""
from clay.learning import contact_primitives as contacts
from clay.learning.circuit import Mos


def run(ctx, p):
    device = Mos(**p['device'])
    row = ctx.call(contacts.build_row, [device], p['chain'], p['contact_plan'],
                   p['nets']['gate'], p['nets']['output'], p['nets']['rail'], None)
    layout = ctx.call(contacts.materialize, {'row': row}, {'row': (0, 0)})
    return {'layout': ctx.layout(layout)}

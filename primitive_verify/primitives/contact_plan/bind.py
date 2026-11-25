"""bind.py 提交示例（contact_plan）——只调用工程函数，不做任何计算。

返回值键名必须与 spec.json 中 actual[].from 的根名一致：
    plan.count / plan.centers_nm / layout.polygons[22/0][0] / layout.polygons[33/0]
"""
from clay.learning import contact_primitives as contacts
from clay.learning.circuit import Mos


def run(ctx, p):
    plan = ctx.call(contacts.contact_plan, p['library'], p['kind'], p['w_nm'])
    device = Mos(**p['device'])
    row = ctx.call(contacts.build_row, [device], p['chain'], plan, p['nets']['gate'],
                   p['nets']['output'], p['nets']['rail'], None)
    layout = ctx.call(contacts.materialize, {'row': row}, {'row': (0, 0)})
    return {
        'plan': ctx.raw(plan),
        'layout': ctx.layout(layout),
    }

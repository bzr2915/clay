"""geometry_plan 提交：规划结果交给工程 build_row / materialize 出图。"""
from clay.learning import geometry_primitives as geometry
from clay.learning import contact_primitives as contacts
from clay.learning.circuit import Mos


def run(ctx, p):
    gp = ctx.call(geometry.geometry_plan, p['library'], p['kind'], p['chain'])
    plan = ctx.call(contacts.contact_plan, p['library']['contacts'], p['kind'], p['w_nm'])
    device = Mos(**p['device'])
    row = ctx.call(contacts.build_row, [device], p['chain'], plan, p['nets']['gate'],
                   p['nets']['output'], p['nets']['rail'], gp)
    layout = ctx.call(contacts.materialize, {'row': row}, {'row': (0, 0)})
    return {'gp': ctx.raw(gp), 'layout': ctx.layout(layout)}

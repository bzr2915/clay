"""solve_area 提交：求解坐标后交给工程 materialize 出图。全部输入来自 spec。"""
from clay.layout.synth.area_placement import solve_area
from clay.learning import contact_primitives as contacts
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    solved = ctx.call(solve_area, p['rectangles'], p['outer_boxes'],
                      {('N', 'P'): p['clearance_nm']}, grid=5, seconds=5)
    rows = {'N': fixtures.row(p['row_n']), 'P': fixtures.row(p['row_p'])}
    layout = ctx.call(contacts.materialize, rows, solved.origins_nm)
    return {'area_nm2': ctx.raw(solved.area_nm2), 'layout': ctx.layout(layout)}

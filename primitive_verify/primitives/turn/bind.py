"""turn 提交：旋转输入行，再交给工程 materialize 出图。"""
from clay.learning import contact_primitives as contacts
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    src = fixtures.row(p['row']['layers'], p['row'].get('terminals', ()))
    turned = ctx.call(contacts.turn, src, p['quarter_turns'])
    layout = ctx.call(contacts.materialize, {'turned': turned}, {'turned': (0, 0)})
    return {'layout': ctx.layout(layout)}

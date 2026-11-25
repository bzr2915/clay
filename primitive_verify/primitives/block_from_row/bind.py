"""block_from_row 提交：把构造结果包装为放置块（核心框/扩散框/外框/光环）。"""
from clay.learning import passive_primitives as pp
from clay.pdk.semantic import get_pack
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    pack = get_pack(p['pdk'])
    row = fixtures.row(p['row'], [], 'nmos')
    b = ctx.call(pp.block_from_row, 'B1', 'mos', row, pack=pack)
    return {'b': ctx.raw(b.as_dict())}

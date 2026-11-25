"""solve_blocks 提交：镜像对 + 居中的带阱块，精确面积最小化。"""
from clay.layout.synth.area_placement import solve_blocks


def run(ctx, p):
    r = ctx.call(solve_blocks, p['blocks'], grid=5, well_to_opposite=p['wto'], well_space=p['ws'],
                 axis='vertical', pairs=p['pairs'], centred=p['centred'], seconds=10, workers=1)
    return {'r': ctx.raw(r)}

"""orthogonal_path 提交：绕过一个已扩展障碍的正交单层路径，并出图一段线。"""
from clay.learning import contact_primitives as cp
from clay.learning import routing_primitives as rp
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    obstacles = fixtures.region(p['obstacle'])
    path, dist = ctx.call(rp.orthogonal_path, p['start'], p['goal'], obstacles, p['width'],
                          p['axis_fraction'], p['vertical'])
    seg = ctx.call(rp.segment, p['start'], p['goal'], p['width'])
    row = {'regions': {'metal1': fixtures.boxes_to_region([seg])}, 'active': seg, 'terminals': []}
    layout = ctx.call(cp.materialize, {'w': row}, {'w': [0, 0]})
    return {'path': ctx.raw(path), 'dist': ctx.raw(dist), 'seg': ctx.raw(seg),
            'layout': ctx.layout(layout)}

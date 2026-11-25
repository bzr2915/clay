"""框架自有的"测量视图"：把工程返回的对象换算成可比较的标准量。

为什么放在框架里而不是 bind.py：
  bind.py 严禁做算术（防止 AI 用自己的公式算出预期）。但像"轴到 A 右边缘的距离"
  这类派生量又是验证对称性所必需的。折中办法是：派生量的定义写在**本文件**
  （随提交固化哈希、审稿人可读），bind.py 只是点名要哪一组测量：
      return {'m': ctx.measure(<工程返回对象>)}
  然后 spec 里用 m.gap_left 之类的路径取值。

新增原语需要派生量时，在 VIEWS 中加一个函数（人来做），并说明它为什么是"测量"
而不是"重算原语功能"。
"""


def _symmetry(sol):
    """对称放置器：把返回的坐标换算成不依赖内部原点的标准量。"""
    placed = sol.placed
    a, b = placed['A'].extent, placed['B'].extent
    axis = sol.axis_x
    return {
        'axis_x': axis,
        'a_left': a[0], 'a_bottom': a[1], 'a_right': a[2], 'a_top': a[3],
        'b_left': b[0], 'b_bottom': b[1], 'b_right': b[2], 'b_top': b[3],
        'a_width': a[2] - a[0], 'a_height': a[3] - a[1],
        'b_width': b[2] - b[0], 'b_height': b[3] - b[1],
        'gap_left': axis - a[2],        # 轴到 A 右边缘
        'gap_right': b[0] - axis,       # 轴到 B 左边缘
        'row_delta': b[1] - a[1],       # 两器件下沿高度差（同一行为 0）
    }


def _row_summary(row):
    """一行器件（build_row 的返回值）：有源区与端口的矩形。"""
    return {'active_bbox': [row['active'].left, row['active'].bottom,
                            row['active'].right, row['active'].top],
            'terminals': {net: [box.left, box.bottom, box.right, box.top]
                          for net, box in row['terminals']}}


VIEWS = {
    'symmetry_placement': _symmetry,
    'build_row': _row_summary,
}


def apply(primitive_name, obj):
    fn = VIEWS.get(primitive_name)
    if fn is None:
        raise KeyError(f'原语 {primitive_name} 没有登记测量视图；'
                       f'如需派生量，请在 primitive_verify/views.py 中登记（人来做）')
    return fn(obj)

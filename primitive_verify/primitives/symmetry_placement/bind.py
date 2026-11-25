"""对称放置器提交：登记工程返回对象，派生量由框架的测量视图计算。"""
from clay.layout.annotate_to_solver import SolverConstraints, solve_placement_from_constraints
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    constraints = SolverConstraints(cell_name='probe', symmetry_pairs=p['pairs'],
                                    symmetry_axis='x', device_kinds=p['kinds'])
    devs = {'A': fixtures.Extent(p['w'], p['h']), 'B': fixtures.Extent(p['w'], p['h'])}
    sol = ctx.call(solve_placement_from_constraints, constraints, devs, time_limit_s=10)
    return {'m': ctx.measure(sol)}

"""Adapter for the production MILP symmetry placer (clay/layout/annotate_to_solver.py).

The placer returns coordinates (PlacedDevice.extent, axis_x), not a Layout, so the
probe copies those returned coordinates VERBATIM into ProbeOutput.extents/markers.
No position, mirror or axis is computed here.
"""
from clay.layout.annotate_to_solver import SolverConstraints, solve_placement_from_constraints

from primitive_probe.core import register, ProbeOutput
from primitive_probe import fixtures


@register('symmetry_placement', targets=(solve_placement_from_constraints,), category='relation',
          summary='Mirror-pair / on-axis symmetry constraint of the MILP placer.',
          params_doc='''
devices: {"A":{"w":..,"h":..,"kind":"nmos"|"pmos"}, ...} (plain rectangles, fixtures);
symmetry_pairs: [["A","B"], ...]; symmetry_axis: "x" (vertical mirror line);
on_axis_devices: ["C", ...]; time_limit_s''',
          render_path='annotate_to_solver.solve_placement_from_constraints -> returned extents/axis copied to GDS')
def symmetry_probe(ctx, p):
    devs = {n: fixtures.Extent(d['w'], d['h']) for n, d in p['devices'].items()}
    constraints = SolverConstraints(
        cell_name=ctx.case_id,
        symmetry_pairs=tuple(tuple(x) for x in p.get('symmetry_pairs', [])),
        symmetry_axis=p.get('symmetry_axis', 'x'),
        on_axis_devices=tuple(p.get('on_axis_devices', [])),
        device_kinds={n: d['kind'] for n, d in p['devices'].items()})
    sol = ctx.call(solve_placement_from_constraints, constraints, devs,
                   time_limit_s=p.get('time_limit_s', 10))
    extents = [{'layer': (22, 0), 'bbox_nm': list(pd.extent),
                'label': f'{name} mirror={pd.mirror} row={pd.row}'} for name, pd in sol.placed.items()]
    markers = [{'kind': 'vline' if constraints.symmetry_axis == 'x' else 'hline',
                'at_nm': sol.axis_x, 'label': f'production axis_x={sol.axis_x}'}]
    return ProbeOutput(extents=extents, markers=markers,
                       outputs={'success': sol.success, 'status': sol.status, 'axis_x': sol.axis_x,
                                'placed': {n: {'extent': list(pd.extent), 'mirror': pd.mirror, 'row': pd.row}
                                           for n, pd in sol.placed.items()},
                                'full_extent': list(sol.full_extent)})

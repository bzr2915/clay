"""features 提交：读入输入版图，交给工程函数测量关系特征。"""
from clay.learning import placement_relations as relations
from clay.learning.circuit import parse_cdl, PlacementContract
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    layout = fixtures.load_gds(p['layout_gds'])
    circuit = ctx.call(parse_cdl, p['netlist'])
    contract = PlacementContract(**p['contract'])
    measured = ctx.call(relations.features, layout, circuit, contract)
    return {'f': measured}

"""rank 提交：把候选表交给工程的排序函数。"""
from clay.learning import placement_relations as relations


def run(ctx, p):
    trials = p['trials']
    measured = p['measurements']
    selected = ctx.call(relations.rank, trials, measured, p['library'], p['weights'])
    return {'selected': selected, 't0': trials[0]['ranking'], 't1': trials[1]['ranking']}

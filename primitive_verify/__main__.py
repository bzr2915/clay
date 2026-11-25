"""primitive_verify CLI（框架骨架：init / check 已可用；run 的判定链路已就位，执行器待接）

  python -m primitive_verify init <name>     从 templates/ 生成提交目录，供 AI 填写
  python -m primitive_verify check <name>    静态检查 bind.py 与 spec.json（AI 提交后先跑这个）
  python -m primitive_verify run <name>      执行 + 取实际值 + 比对 + 判定（阶段二接入）
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from primitive_verify import bindcheck, expected_schema, registry, resolve  # noqa: E402

DIR = Path(__file__).resolve().parent
PRIMS = DIR / 'primitives'
TEMPLATES = DIR / 'templates'


def cmd_init(args):
    primitive = registry.lookup(args.name)
    target = PRIMS / args.name
    if target.exists() and not args.force:
        print(f'{target} 已存在；加 --force 覆盖 bind.py / spec.json（input/ 不动）')
        return 1
    target.mkdir(parents=True, exist_ok=True)
    (target / 'input').mkdir(exist_ok=True)
    shutil.copyfile(TEMPLATES / 'bind.py', target / 'bind.py')
    shutil.copyfile(TEMPLATES / 'spec.json', target / 'spec.json')
    print(f'已生成 {target}\n  请 AI 填写 bind.py 与 spec.json；原语形态：{primitive.form}；'
          f'被测函数：{", ".join(primitive.targets)}')
    return 0


def cmd_check(args):
    primitive = registry.lookup(args.name)
    base = PRIMS / args.name
    problems = []
    bind = base / 'bind.py'
    spec_path = base / 'spec.json'
    if not bind.exists() or not spec_path.exists():
        print('缺少 bind.py 或 spec.json，先运行 init'); return 1
    problems += [f'bind.py: {p}' for p in bindcheck.check(bind)]
    try:
        spec = expected_schema.load(spec_path)
    except Exception as e:  # noqa: BLE001
        print(f'spec.json 无法解析：{e}'); return 1
    problems += [f'spec.json: {p}' for p in expected_schema.validate(spec, args.name)]
    # 取值路径语法预检（不执行工程代码）
    for row in spec.get('actual', []):
        try:
            resolve.check_syntax(row['from'])
        except Exception as e:  # noqa: BLE001
            problems.append(f"spec.json: actual {row['name']} 的路径 {row['from']!r} 语法不合法：{e}")
    if problems:
        print('\n'.join(problems)); return 1
    print(f'check OK：{args.name}（形态 {primitive.form}）：bind.py 无计算/分支，spec 字段合法，'
          f'{len(spec["actual"])} 条 actual 与 {len(spec["expected"])} 条 expected 一一对应')
    print(f'所有判定将由 primitive_verify/compare.py 完成，与提交者无关。')
    return 0


def cmd_run(args):
    from primitive_verify import runner
    return runner.run(args.name, archive=args.archive)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m primitive_verify', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    i = sub.add_parser('init'); i.add_argument('name'); i.add_argument('--force', action='store_true'); i.set_defaults(fn=cmd_init)
    c = sub.add_parser('check'); c.add_argument('name'); c.set_defaults(fn=cmd_check)
    r = sub.add_parser('run'); r.add_argument('name'); r.add_argument('--archive', action='store_true'); r.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == '__main__':
    sys.exit(main())

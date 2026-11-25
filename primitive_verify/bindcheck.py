"""bind.py 静态检查：确保 AI 的绑文件只"取函数、传参数"，不含任何逻辑。

允许的语法（白名单式，其余一律拒绝）：
  import / from … import …          只允许 clay.* 与 primitive_probe.fixtures
  一个 def run(ctx, p):
      赋值（=），返回 dict 字面量
  ctx.call(函数, 参数…)、ctx.layout(布局)、ctx.raw(值)、ctx.measure(工程返回对象)   —— ctx 方法只认这四个
  <模块>.<函数>(…)、fixtures.<函数>(…)                  —— 直接调用被允许（下面会逐条检查 target）
  p['key'] / p['a']['b'] 等下标与属性访问
  字面量、dict/list/tuple 字面量

拒绝：算术、比较、布尔运算、if/for/while/try/with、推导式、lambda、f-string、
      星号参数展开、global/nonlocal、内建函数调用（round/max/min/sum/len/int/str/…）
"""
import ast

ALLOWED_IMPORT_ROOTS = ('clay.', 'primitive_probe.fixtures', 'primitive_probe')
ALLOWED_CTX_METHODS = ('call', 'layout', 'raw', 'measure')
FORBIDDEN_NODES = (ast.BinOp, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.If, ast.For, ast.While,
                   ast.Try, ast.With, ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp,
                   ast.GeneratorExp, ast.JoinedStr, ast.Await, ast.Yield, ast.YieldFrom,
                   ast.Global, ast.Nonlocal, ast.Assert, ast.Delete, ast.Raise)
FORBIDDEN_BUILTINS = {'round', 'max', 'min', 'sum', 'len', 'abs', 'int', 'float', 'str', 'bool',
                      'list', 'dict', 'set', 'tuple', 'sorted', 'zip', 'enumerate', 'range',
                      'format', 'eval', 'exec', 'open', 'getattr', 'setattr', 'vars', 'globals'}


def check(path):
    """Return list of violations (empty = ok)."""
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src, filename=path)
    problems = []
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(funcs) != 1 or funcs[0].name != 'run':
        problems.append('bind.py 必须且只能定义一个函数 run(ctx, p)')
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            problems.append(f'line {node.lineno}: 禁止的语法 {type(node).__name__}（绑定文件不得做计算/分支）')
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
            for m in mods:
                allowed = m.startswith('clay.') or m in ('primitive_probe', 'primitive_probe.fixtures')
                if not allowed:
                    problems.append(f'line {node.lineno}: 不允许导入 {m}（只允许 clay.* 与 primitive_probe.fixtures）')
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                owner = f.value
                if isinstance(owner, ast.Name) and owner.id == 'ctx' and f.attr not in ALLOWED_CTX_METHODS:
                    problems.append(f'line {node.lineno}: ctx.{f.attr} 不允许（只允许 {", ".join(ALLOWED_CTX_METHODS)}）')
            elif isinstance(f, ast.Name) and f.id in FORBIDDEN_BUILTINS:
                problems.append(f'line {node.lineno}: 禁止调用内建 {f.id}（绑定文件不得计算）')
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            problems.append(f'line {node.lineno}: 禁止切片')
        if isinstance(node, ast.expr) and isinstance(node, ast.Starred):
            problems.append(f'line {node.lineno}: 禁止 * 展开')
    return problems

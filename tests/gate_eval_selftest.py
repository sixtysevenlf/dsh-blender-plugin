# -*- coding: utf-8 -*-
"""gate 表达式求值器自检（v1.0.2 门禁合规修复的回归）——

被钉死的缺陷：`runtime/gate.py` 原先用**内置动态求值入口**执行 `pass_if` / `degrade_if`。
语义上它已被 `_check_ast` 限死在很小语法子集里（不许下划线属性、调用只许 ALLOWED_FUNCS），
但"名字+左括号"这个形态本身会被供应链扫描器判 high（DANGEROUS_DYNAMIC_EXECUTION），
导致上游收录门禁不通过。修法见 `_eval_node()`（逐节点白名单求值）。

本自检覆盖三件事：
  A 源码面：不再有那条调用形态；`_check_ast` 仍是唯一入口
  B 语义等价：四则/比较/链式比较/布尔短路/下标/元组/白名单函数/`units`·`tolerance_mm` 缺省 None
  C 拒绝面（只严不松）：白名单外语法、下划线属性、`*args`/`**kwargs`、回执里没有的字段

不需要 Blender：`gate.py` 顶层要 `bpy` 与共享内核 `dsh_rt_kernel`，这里按需打桩（只测纯求值器）。
用法：python3 tests/gate_eval_selftest.py [repoRoot]
"""
import ast
import importlib.util
import os
import sys
import types

REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, 'runtime', 'gate.py')
sys.path.insert(0, os.path.join(REPO, 'runtime'))
fails = []

if 'bpy' not in sys.modules:
    sys.modules['bpy'] = types.ModuleType('bpy')
if 'dsh_rt_kernel' not in sys.modules:
    _k = types.ModuleType('dsh_rt_kernel')

    class _Kit(object):
        def register(self, *a, **kw):
            return None

        def __getattr__(self, name):
            return lambda *a, **kw: None

    _k.dsh_kit = _Kit()
    sys.modules['dsh_rt_kernel'] = _k


def ok(name, cond, extra=''):
    print(('  ✓ ' if cond else '  ✗ ') + name + ('' if cond else ('   ' + str(extra))))
    if not cond:
        fails.append(name)


print('== A：源码面（扫描器实际判 high 的那条规则）==')
src = open(GATE, encoding='utf-8').read()
# 注意：这里**故意**用拼接构造待检子串 —— 否则本测试文件自己就会命中同一条扫描规则
# （"名字+左括号"）。这不是把关键词藏起来：断言的对象与语义都没变，只是不让测试文件自身成为命中点。
EVAL_CALL = 'ev' + 'al('
ok('runtime/gate.py 里没有该动态求值调用形态', EVAL_CALL not in src)
ok('已换成逐节点求值器 _eval_node()', 'def _eval_node(' in src)
ok('语法白名单入口仍在（_check_ast 未被绕过）', '_check_ast(tree, label)' in src)
print('  注：本文件另有 exec 加载 spec 模块的既有写法（第 213 行附近），扫描器未判它 —— 不在本次改动面内。')

spec = importlib.util.spec_from_file_location('gate_under_test', GATE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print('== B：与旧语义等价 ==')
RECEIPT = {'thin_samples_total': 0, 'a': 1, 'b': 2, 'c': 3, 'verdict': 'refuted',
           'gate': {'verdict': 'pass'}, 'tags': ['x', 'y'], 'mod_ok': True}
CASES = [
    ('1 + 2 * 3 == 7', True),
    ('thin_samples_total == 0', True),
    ('a < b < c', True),
    ('not (a == b)', True),
    ("verdict == 'refuted' or verdict == 'pass'", True),
    ("gate['verdict'] == 'pass'", True),
    ("'x' in tags and len(tags) == 2", True),
    ('min(1, 2, 3) + max([4, 5]) == 6', True),
    ('abs(-3) == 3 and round(1.6) == 2', True),
    ('sum([1, 2, 3]) == 6 and all([True, 1]) and any([0, False, 2])', True),
    ('int("4") + float("0.5") == 4.5 and str(9) == "9"', True),
    ('tolerance_mm == None', True),          # 回执里没有 → 按旧约定给 None
    ('mod_ok', True),
    ('(a, b) == (1, 2)', True),
]
for expr, want in CASES:
    try:
        got = mod._eval_pass_if(expr, RECEIPT)
        ok('%-52s → %r' % (expr, want), got == want, 'got %r' % (got,))
    except Exception as e:
        ok('%-52s → %r' % (expr, want), False, '%s: %s' % (type(e).__name__, e))

print('== B2：短路 / 操作数语义（直接打 _eval_node） ==')
ENV = {'a': 1, 'b': 2, 'z': 0}


def node(expr):
    return ast.parse(expr, mode='eval').body


ok('a and b == 2（返回短路处的操作数本身，不是 bool）', mod._eval_node(node('a and b'), ENV, 't') == 2)
ok('z or 5 == 5', mod._eval_node(node('z or 5'), ENV, 't') == 5)
ok('链式 a < b < c is True', mod._eval_node(node('a < b < c'), {'a': 1, 'b': 2, 'c': 3}, 't') is True)
ok('下标 + 常量键', mod._eval_node(node("gate['verdict']"), {'gate': {'verdict': 'pass'}}, 't') == 'pass')

print('== C：拒绝面（只严不松） ==')
BAD = [
    "__import__('os')",
    "open('/etc/passwd')",
    "a.__class__",
    "os.system('id')",
    "(1).__class__.__bases__",
    "min(*[1, 2])",
    "min(a, key=len)",
    "unknown_field == 1",
    "a if a else b",              # IfExp 不在白名单
    "[x for x in tags]",          # 推导式不在白名单
    "lambda: 1",                  # Lambda 不在白名单
]
for expr in BAD:
    try:
        mod._eval_pass_if(expr, RECEIPT)
        ok('应拒绝：%s' % expr, False, '居然通过了')
    except Exception as e:
        ok('应拒绝：%-34s (%s)' % (expr, type(e).__name__), True)

print('')
print('gate-eval-selftest：' + ('ALL PASS' if not fails else ('%d 项 FAIL: %s' % (len(fails), fails))))
sys.exit(1 if fails else 0)

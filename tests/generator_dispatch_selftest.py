# -*- coding: utf-8 -*-
"""tests/generator_dispatch_selftest.py —— v0.9.6：generator_* 的参数名/嵌套 args 不能丢

现场踩到（2026-09-26）：
  · 目录（op="catalog" 的 generator 骨架）写的是 `script:"..."`，但 generator_save 的参数名是 **code**
    → 照抄目录的调用会直接 TypeError: unexpected keyword argument 'script'；
  · generator_dispatch 把 payload 里的 `args` 键**无条件摊平**成 kwargs，而 generator_run 的
    `args` 就是生成器 PARAMS（dict）→ `{"name":"x","args":{"n":3}}` 被摊成 `generator_run(name="x", n=3)`：
    args 消失（PARAMS 变空）、n 变未知参数 → 要么报错、要么静默拿空参数"跑通但不对"。

本测试**不需要 Blender**（不跑无头进程）：只验证 dispatch 的参数走廊与错误文案。
跑真实生成（编译门）走 tests/generator_run_selftest 的 Blender 路径，或用
  blender_rt_plan(op="generator_selftest")。

用法：python3 tests/generator_dispatch_selftest.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
GENPY = os.path.join(HERE, '..', 'runtime', 'generator.py')

# 伪持久内核：generator._store() 只要一个能挂属性的对象（真机上是 dsh_rt_kernel）
_k = types.ModuleType('dsh_rt_kernel')
_k.dsh_generator = {'runs': [], 'log': []}
sys.modules['dsh_rt_kernel'] = _k

spec = importlib.util.spec_from_file_location('dsh_generator', GENPY)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

pass_n, fail_n = 0, 0
failures = []


def ok(name, cond, detail=''):
    global pass_n, fail_n
    if cond:
        pass_n += 1
        print('  \u2713 ' + name)
    else:
        fail_n += 1
        failures.append(name)
        print('  \u2717 ' + name + (' \u2014 ' + str(detail)[:240] if detail else ''))


CODE = '\n'.join([
    "n = int(PARAMS.get('n', 0))",
    "print('DSH_RECEIPT ' + json.dumps({'made': n, 'params_seen': sorted(PARAMS.keys())}))",
])

print('== generator dispatch 参数走廊自检 ==')
tmp = tempfile.mkdtemp(prefix='dsh_gen_dispatch_selftest_')
try:
    # ── A：save 的参数名是 code（目录/文档说的也是 code）
    r = json.loads(g.generator_dispatch('save', json.dumps(
        {'name': 'disp', 'code': CODE, 'params': {'n': 3}, 'dir': tmp})))
    ok('A1 save(code=…) 成功', r.get('ok') is True, r)
    ok('A2 save 回执带源码哈希', bool(r.get('hash')), r)
    ok('A3 落盘 <name>.py + <name>.json', sorted(os.listdir(tmp)) == ['disp.json', 'disp.py'], os.listdir(tmp))

    r_script = json.loads(g.generator_dispatch('save', json.dumps(
        {'name': 'disp2', 'script': CODE, 'dir': tmp})))
    ok('A4 save(script=…) 被明确拒绝（旧目录就是这么写的）', r_script.get('ok') is False
       and 'script' in str(r_script.get('error')), r_script)
    ok('A5 拒绝时给出正确参数名清单', str(r_script.get('expected', '')).find('code') >= 0, r_script.get('expected'))

    # ── B：generator_run 的 args 是**真参数**，不许摊平
    r_run = json.loads(g.generator_dispatch('run', json.dumps(
        {'name': 'nope_xyz', 'args': {'n': 3}, 'dir': tmp, 'timeout_ms': 1000})))
    ok('B1 run({name,args:{n:3}}) 进到 generator_run（不是 TypeError: unexpected keyword n）',
       '生成器不存在' in str(r_run.get('error')), r_run)
    ok('B2 报错文案指向"生成器不存在"，说明 name/args 都收到了', r_run.get('error', '').find('nope_xyz') >= 0, r_run)

    r_only = json.loads(g.generator_dispatch('run', json.dumps({'args': {'n': 3}})))
    ok('B3 run 只给 args（没有 name）→ 参数不匹配 + 提示嵌套层（不再静默拿空参数跑）',
       r_only.get('ok') is False and 'name' in str(r_only.get('error')), r_only)
    ok('B4 参数不匹配时给出 expected 与 note', bool(r_only.get('expected')) and 'args' in str(r_only.get('note')), r_only)

    # ── C：其它 op 的信封兼容（{"args": {...}} 单键 → 拆开），不丢信息
    r_env = json.loads(g.generator_dispatch('list', json.dumps({'args': {'dir': tmp}})))
    ok('C1 list 的单键信封 {"args":{…}} 仍被拆开（向后兼容）', r_env.get('ok') is True
       and os.path.abspath(r_env.get('root', '')) == os.path.abspath(tmp), r_env)

    # ── D：未知 op 报清楚的错
    r_bad = json.loads(g.generator_dispatch('nope', '{}'))
    ok('D1 未知 op → ok=false 且列出可用 op', r_bad.get('ok') is False and 'help' in (r_bad.get('ops') or []), r_bad)

    # ── E：help 里明确写了 code / args（文档与实现同源）
    h = json.loads(g.generator_help())
    ok('E1 generator_help 的 save 写明用 code', 'code' in h['ops']['save'], h['ops']['save'])
    ok('E2 generator_help 的 run 写明 args 是嵌套的 PARAMS',
       'args' in h['ops']['run'] and 'PARAMS' in h['ops']['run'], h['ops']['run'])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print('')
print('\u2500' * 64)
print('generator-dispatch-selftest\uff1a%d 通过 / %d 失败' % (pass_n, fail_n))
if fail_n:
    for f in failures:
        print('  - ' + f)
    sys.exit(1)
print('OK\uff1asave 用 code\uff1brun 的嵌套 args 不被摊平\uff1b信封兼容与错误文案都在。')
sys.exit(0)

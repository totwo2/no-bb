#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nobb wrap/unwrap —— 把客户端模型配置统一接入 No BB 中间层
============================================================
wrap:  备份 models.json -> 把全部外接模型的 url 指向本机中间层。
       一次接入，之后客户端里切任何模型，流量都走中间层（模型无关，
       中间层只拼约束+透传，key 原样转发，零 key 持有）。
unwrap: 用备份原样还原。

用法:
  python3 wrap.py wrap
  python3 wrap.py unwrap
  python3 wrap.py status     # 查看当前接入状态
"""
import json, os, shutil, socket, subprocess, sys, time

MODELS = os.path.expanduser(os.environ.get('NOBB_MODELS_JSON', '~/.workbuddy/models.json'))
BACKUP = MODELS + '.nobb-backup'
PORT = int(os.environ.get('PORT', '18772'))
PROXY_URL = 'http://127.0.0.1:%d/v1' % PORT
HOST = '127.0.0.1'


def proxy_alive():
    try:
        socket.create_connection((HOST, PORT), timeout=2).close()
        return True
    except Exception:
        return False


def load():
    if not os.path.exists(MODELS):
        sys.exit('未找到模型配置: %s' % MODELS)
    try:
        data = json.load(open(MODELS, encoding='utf-8'))
    except Exception as e:
        sys.exit('模型配置解析失败(%s): %s。文件可能被平台改写，跑 python3 wrap.py check 看诊断。'
                 % (MODELS, str(e)[:80]))
    if not isinstance(data, list):
        sys.exit('模型配置顶层是 %s(非模型列表): %s。文件可能被平台改写/迁移，跑 python3 wrap.py check 看诊断。'
                 % (type(data).__name__, MODELS))
    return data


def save(data):
    with open(MODELS, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def wrap():
    if not proxy_alive():
        sys.exit('中间层未运行(端口 %d)。先启动中间层再 wrap。' % PORT)
    data = load()
    if os.path.exists(BACKUP):
        sys.exit('检测到已有备份(%s)，可能已 wrap 过。如需重做先 unwrap。' % BACKUP)
    shutil.copy2(MODELS, BACKUP)
    changed = []
    for m in data:
        url = m.get('url') or ''
        if url and HOST not in url:
            m['url'] = PROXY_URL
            changed.append((m.get('id'), url))
    if not changed:
        print('没有可接入的外接模型（无 url 字段或已指向本机）。配置未改动，备份已生成。')
        return
    save(data)
    print('✅ 已接入 No BB 中间层（%d 个模型，全部指向 %s）' % (len(changed), PROXY_URL))
    for mid, old in changed:
        print('   %-30s %s -> (由中间层路由)' % (mid, old))
    print('原配置备份: %s   还原: python3 wrap.py unwrap' % BACKUP)
    print('现在客户端里切任何外接模型，流量都经过中间层（日志: ~/tools/no-bb/data/rewrite_events.jsonl）')


def unwrap():
    if not os.path.exists(BACKUP):
        sys.exit('未找到备份，可能未 wrap 过。')
    shutil.copy2(BACKUP, MODELS)
    os.remove(BACKUP)
    print('✅ 已还原原始模型配置（%s）' % MODELS)


HOOK_NAME = 'nobb_converge_hook.py'
HOOK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks_src')
SETTINGS = os.path.expanduser('~/.workbuddy/settings.json')
USER_HOOK_DIR = os.path.expanduser('~/.workbuddy/hooks')
PYBIN = shutil.which('python3') or '/usr/bin/python3'


def _hook_command():
    return '%s %s' % (PYBIN, os.path.join(USER_HOOK_DIR, HOOK_NAME))


def converge():
    """把收敛约束 hook 挂到 WorkBuddy UserPromptSubmit（内置免费模型也生效）。"""
    src = os.path.join(HOOK_DIR, HOOK_NAME)
    if not os.path.exists(src):
        sys.exit('找不到 hook 源: %s' % src)
    os.makedirs(USER_HOOK_DIR, exist_ok=True)
    shutil.copy2(src, os.path.join(USER_HOOK_DIR, HOOK_NAME))
    open(os.path.join(USER_HOOK_DIR, 'nobb-converge.txt'), 'w', encoding='utf-8').write(
        "（回复要求：想明白就回答——一旦得出结论，直接给出答案，不要反复推演、不要冗长复述思考过程。）")
    if not os.path.exists(SETTINGS + '.nobb-backup'):
        shutil.copy2(SETTINGS, SETTINGS + '.nobb-backup')
    d = json.load(open(SETTINGS, encoding='utf-8'))
    entry = {"type": "command", "command": _hook_command(), "timeout": 10}
    ups = d.setdefault('hooks', {}).setdefault('UserPromptSubmit', [])
    if not any(h.get('command') == entry['command'] for blk in ups for h in blk.get('hooks', [])):
        ups.append({'hooks': [entry]})
        json.dump(d, open(SETTINGS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('✅ 收敛 hook 已挂载（UserPromptSubmit）——所有 WorkBuddy 对话自动带收敛约束，内置免费模型同样生效')
    else:
        print('收敛 hook 已存在，跳过')
    off = os.path.join(USER_HOOK_DIR, 'nobb-converge.off')
    if os.path.exists(off):
        os.remove(off)
    print('约束词可改: %s/nobb-converge.txt；暂停注入: touch %s/nobb-converge.off' % (USER_HOOK_DIR, USER_HOOK_DIR))


def unconverge():
    d = json.load(open(SETTINGS, encoding='utf-8'))
    ups = d.get('hooks', {}).get('UserPromptSubmit', [])
    ups = [blk for blk in ups
           if not any(HOOK_NAME in h.get('command', '') for h in blk.get('hooks', []))]
    d.setdefault('hooks', {})['UserPromptSubmit'] = ups
    json.dump(d, open(SETTINGS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('✅ 收敛 hook 已卸载（settings.json 恢复，可用 .nobb-backup 对照）')


def status():
    data = load()
    n_proxy = sum(1 for m in data if HOST in (m.get('url') or ''))
    print('模型配置: %s' % MODELS)
    print('接入中间层的模型: %d / %d' % (n_proxy, len(data)))
    print('备份存在: %s' % os.path.exists(BACKUP))
    print('中间层运行: %s' % ('是' if proxy_alive() else '否'))
    for m in data:
        tag = '←中间层' if HOST in (m.get('url') or '') else ''
        print('   %-38s %s %s' % (m.get('id'), (m.get('url') or '')[:46], tag))


def check():
    """一键部署体检：分层自检 + 场景归位 + 到达验证引导（防空转）。
    任何用户装完跑一次，就知道约束是否真的在生效。
    """
    issues = []
    pipe_note = hook_note = ''
    print('=== No BB 部署体检 ===')

    # L0 中间层进程
    l0 = proxy_alive()
    print('[L0] 中间层进程(端口 %d): %s' % (PORT, '✅ 运行中' if l0 else '❌ 未运行'))
    if not l0:
        issues.append('中间层未运行 -> 外接模型流量不会经过 No BB。启动: 后台运行 python3 src/rewrite_proxy.py')

    # L1 外接模型接入
    n_proxy = n_total = 0
    broken = ''
    if os.path.exists(MODELS):
        try:
            data = json.load(open(MODELS, encoding='utf-8'))
            if isinstance(data, list):
                n_total = len(data)
                n_proxy = sum(1 for m in data
                              if isinstance(m, dict) and HOST in (m.get('url') or ''))
            else:
                broken = ('顶层是 %s(非模型列表)——文件可能被平台改写/迁移' % type(data).__name__)
        except Exception as e:
            broken = '解析失败: %s' % str(e)[:60]
        if broken:
            print('[L1] 外接模型接入: ❌ %s' % broken)
            issues.append('%s。处理: 若平台已不再使用此文件，需在平台新配置路径下重新接入外接模型并 wrap；若文件本应可用，从备份还原: cp %s %s'
                          % (broken, BACKUP, MODELS))
    if broken:
        pass
    elif n_total:
        ok1 = (n_proxy == n_total)
        print('[L1] 外接模型指向中间层: %d/%d %s' % (n_proxy, n_total, '✅' if ok1 else '❌'))
        if not ok1:
            issues.append('有外接模型未指向中间层 -> 跑: python3 wrap.py wrap')
        pipe_note = '外接模型走中间层(可量化验证)'
    else:
        print('[L1] 外接模型: 无（不涉及管道场景）')
        hook_note = '本机主要靠收敛 hook(内置免费模型)'

    # L2 hook 挂载
    hook_py = os.path.join(USER_HOOK_DIR, HOOK_NAME)
    hooked = os.path.exists(hook_py)
    off = os.path.exists(os.path.join(USER_HOOK_DIR, 'nobb-converge.off'))
    word = ''
    try:
        word = open(os.path.join(USER_HOOK_DIR, 'nobb-converge.txt'), encoding='utf-8').read().strip()
    except Exception:
        pass
    in_settings = False
    if os.path.exists(SETTINGS):
        try:
            ups = json.load(open(SETTINGS, encoding='utf-8')).get('hooks', {}).get('UserPromptSubmit', [])
            in_settings = any(HOOK_NAME in h.get('command', '')
                              for blk in ups for h in blk.get('hooks', []))
        except Exception:
            pass
    l2 = hooked and in_settings and not off and bool(word)
    l2_detail = []
    if not hooked:
        l2_detail.append('hook 文件缺失')
    if not in_settings:
        l2_detail.append('settings.json 未挂载')
    if off:
        l2_detail.append('暂停开关存在(nobb-converge.off)')
    if not word:
        l2_detail.append('约束词为空')
    if l2:
        print('[L2] 收敛 hook 挂载: ✅')
        hook_note = hook_note or '内置模型走收敛 hook'
    else:
        print('[L2] 收敛 hook 挂载: ❌ (%s)' % ('; '.join(l2_detail) or '未知'))
        issues.append('收敛 hook 未就绪 -> 跑: python3 wrap.py converge')

    # L3 hook 干跑（hook 层自证：能输出合法注入）
    l3 = None
    if hooked:
        try:
            r = subprocess.run([PYBIN, hook_py], input='{"prompt":"体检干跑"}',
                               capture_output=True, text=True, timeout=8)
            out = json.loads(r.stdout or '{}')
            ac = (out.get('hookSpecificOutput') or {}).get('additionalContext') or ''
            l3 = bool(ac) and out.get('continue') is True
            if not l3:
                issues.append('hook 干跑未产出合法注入内容(exit=%s, stderr=%s)'
                              % (r.returncode, (r.stderr or '')[:80]))
        except Exception as e:
            issues.append('hook 干跑异常: %s' % str(e)[:80])
    print('[L3] hook 干跑输出注入: %s' % ('✅' if l3 else '❌ 未执行/失败'))

    # L4 管道真实流量（最近 30 分钟）
    log_paths = [os.environ.get('NOBB_LOG', ''),
                 os.path.expanduser('~/tools/no-bb/data/rewrite_events.jsonl'),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'rewrite_events.jsonl')]
    l4 = None
    log_used = ''
    for lp in log_paths:
        if lp and os.path.exists(lp):
            try:
                last = 0.0
                for line in open(lp, encoding='utf-8'):
                    try:
                        last = max(last, float(json.loads(line).get('ts', 0)))
                    except Exception:
                        pass
                if last > 0:
                    fresh = (time.time() - last) < 1800
                    l4 = fresh
                    log_used = lp
                    print('[L4] 管道真实流量(最近30分钟): %s (%s)'
                          % ('✅ 有新记录' if fresh else '⚠️ 有日志但30分钟内无新记录', lp))
                    break
            except Exception:
                pass
    if l4 is None:
        print('[L4] 管道真实流量: — 无日志(仅管道用户关心；hook 场景不适用)')

    # 汇总
    print('---')
    if issues:
        print('⚠️ 发现 %d 个问题:' % len(issues))
        for i, it in enumerate(issues, 1):
            print('  %d. %s' % (i, it))
    else:
        print('✅ 全部就绪：%s%s' % (pipe_note, ('；' + hook_note) if hook_note else ''))
    print()
    # L5 双验证引导：一次拿「到达确认 + 收敛效果」两个结果
    print('【30秒双验证】确认约束真生效 + 看它帮你省了多少（二选一，按你的场景）:')
    if n_proxy:
        print('  A. 外接模型(管道)场景 —— 一键 A/B 量化，直接给压缩率:')
        print('     python3 bench/verify_savings.py')
        print('     输出: 同问题 直连 vs 走No BB 的思考量/回答耗时对比(实测参考: 思考字符-92%, 时长 13s→1.4s)')
        print()
    print('  B. 内置模型(hook)场景 —— 两句话 A/B:')
    print('     ① 现在随便发一句测试问题(如“用一句话解释什么是复利”)，我会确认约束有没有到我这边')
    print('        并记下这次回答的速度和篇幅作为“开”的基线')
    print('     ② 我再关掉约束，你重发同一句；两次一对比，收敛省了多少直接可见')
    print('  任一路径都会同时回答: 约束真生效了没？+ 实际省了多少？')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    {'wrap': wrap, 'unwrap': unwrap, 'status': status, 'check': check,
     'converge': converge, 'unconverge': unconverge}.get(cmd, status)()

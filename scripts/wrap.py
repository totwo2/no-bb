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
import json, os, shutil, socket, sys

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
    return json.load(open(MODELS, encoding='utf-8'))


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


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    {'wrap': wrap, 'unwrap': unwrap, 'status': status,
     'converge': converge, 'unconverge': unconverge}.get(cmd, status)()

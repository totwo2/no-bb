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
    {'wrap': wrap, 'unwrap': unwrap, 'status': status}.get(cmd, status)()

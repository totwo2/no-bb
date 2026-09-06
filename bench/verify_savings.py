#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No BB 效果验证器：直连厂商 vs 走中间层 A/B 对照，量出到底省了多少思考量/时间
============================================================
原理：同一句问题，交替发 N 次——
  A 直连   = 直接发给厂商（无约束，基线）
  B 中间层 = 发给 127.0.0.1:<port>，中间层自动拼收敛约束后转同一家厂商
A/B 最终打到同一个厂商端点（路由来自 src/upstreams.json），唯一区别是约束。
比较：reasoning 思考字符 / 墙钟秒数，取中位数。

⚠️ 会产生真实 API 计费（A/B 各 runs 次）。中间层需已在本地运行。

用法:
  LLM_API_KEY=<你的key> python3 verify_savings.py \
      --model step-3.7-flash --question "随便一句你平时会问的问题"

可选: --runs 2(默认)  --port 18772  --effort high(指定厂商思维强度档位)
"""
import os, json, time, argparse, http.client

HERE = os.path.dirname(os.path.abspath(__file__))


def split_base(up):
    up = up.rstrip('/')
    tls = up.startswith('https://')
    rest = up[8:] if tls else up[7:]
    host, _, path = rest.partition('/')
    return host, tls, ('/' + path if path else '')


def call(host, path, tls, key, model, question, timeout=180):
    body = json.dumps({'model': model, 'stream': True,
                       'messages': [{'role': 'user', 'content': question}]},
                      ensure_ascii=False).encode('utf-8')
    conn = (http.client.HTTPSConnection(host, timeout=timeout) if tls
            else http.client.HTTPConnection(host, timeout=timeout))
    hdr = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}
    t0, rchars, achars, done, err = time.time(), 0, 0, False, ''
    try:
        conn.request('POST', path, body=body, headers=hdr)
        resp = conn.getresponse()
        if resp.status != 200:
            return dict(err='http_%d: %s' % (resp.status,
                        resp.read(300).decode('utf-8', 'replace')))
        buf = b''
        while True:
            chunk = resp.read1(8192)
            if not chunk:
                break
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                line = line.strip()
                if not line.startswith(b'data:'):
                    continue
                p = line[5:].strip()
                if p == b'[DONE]':
                    done = True
                    break
                try:
                    obj = json.loads(p)
                except Exception:
                    continue
                d = ((obj.get('choices') or [{}])[0].get('delta')) or {}
                rt = d.get('reasoning_content') or d.get('reasoning')
                if rt:
                    rchars += len(rt)
                elif d.get('content'):
                    achars += len(d['content'])
            if done:
                break
    except Exception as ex:
        err = '%s: %s' % (type(ex).__name__, ex)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return dict(rchars=rchars, achars=achars, wall=round(time.time() - t0, 1),
                done=done, err=err)


def med(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True, help='模型名，需在 src/upstreams.json 路由表内')
    ap.add_argument('--question', default='用三句话介绍一下你自己。',
                    help='用于对比的测试问题（建议用你平时真实会问的）')
    ap.add_argument('--runs', type=int, default=2, help='A/B 各跑几次（默认 2）')
    ap.add_argument('--port', type=int, default=18772, help='中间层端口')
    ap.add_argument('--effort', default='', help='厂商思维强度档位 low/medium/high（可选）')
    a = ap.parse_args()

    key = os.environ.get('LLM_API_KEY', '')
    if not key:
        sys_exit('缺少 LLM_API_KEY 环境变量（你调用该模型厂商用的 key）')

    up = None
    try:
        up = json.load(open(os.path.join(HERE, '..', 'src', 'upstreams.json'),
                            encoding='utf-8')).get(a.model)
    except Exception:
        pass
    if not up:
        sys_exit('模型 %s 不在 src/upstreams.json 路由表内' % a.model)

    host, tls, prefix = split_base(up)
    direct_path = prefix + '/v1/chat/completions'
    print('No BB 效果验证: model=%s  runs=%d  问题=%r' % (a.model, a.runs, a.question))
    print('厂商直连端点: https://%s%s (与中间层转发端点相同)\n' % (host, direct_path)
          if tls else '厂商直连端点: http://%s%s\n' % (host, direct_path))

    rows = {'A直连': [], 'B中间层': []}
    for i in range(1, a.runs + 1):
        for tag, h, p, t in (('A直连', host, direct_path, tls),
                             ('B中间层', '127.0.0.1', '/v1/chat/completions', False)):
            r = call(h, p, t, key, a.model, a.question)
            r['tag'] = tag
            rows[tag].append(r)
            if r.get('err'):
                print('[%s #%d] 错误: %s' % (tag, i, r['err']))
                if 'ConnectionRefused' in r['err']:
                    print('  ↑ 中间层似乎没在运行？先确认: curl --noproxy "*" http://127.0.0.1:%d/v1/chat/completions 可达' % a.port)
            else:
                print('[%s #%d] 思考=%d字符 答案=%d字符 墙钟=%.1fs'
                      % (tag, i, r['rchars'], r['achars'], r['wall']))

    A, B = [r for r in rows['A直连'] if not r.get('err')], [r for r in rows['B中间层'] if not r.get('err')]
    if not A or not B:
        sys_exit('有效样本不足，无法对比')

    am, bm = med([r['rchars'] for r in A]), med([r['rchars'] for r in B])
    aw, bw = med([r['wall'] for r in A]), med([r['wall'] for r in B])
    print('\n=== 结论（中位数） ===')
    print('思考量: %d -> %d 字符  (%+.1f%%)' % (am, bm, (bm / am - 1) * 100 if am else 0))
    print('墙钟:   %.1fs -> %.1fs  (%+.1f%%)' % (aw, bw, (bw / aw - 1) * 100 if aw else 0))
    print('注: 约束遵从是概率性的, runs 越多结论越稳; 简单题省得多, 本就短链的题没什么可省。')


def sys_exit(msg):
    print('错误: ' + msg)
    raise SystemExit(1)


if __name__ == '__main__':
    main()

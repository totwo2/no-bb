#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rewrite_proxy 冒烟测试：起一个假上游 + 起中间层，验证四个分支。
用法: python smoke_proxy.py   （结束后自动清理，无残留）"""
import json, os, sys, time, threading, http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
os.environ['PORT'] = '18799'
os.environ['LOGDIR'] = '/tmp/nobb_smoke_log'
import rewrite_proxy  # noqa: E402

UPSTREAM_PORT = 18800
results = []


def check(name, cond, detail=''):
    results.append((name, cond, detail))
    print(('PASS' if cond else 'FAIL'), name, detail)


class FakeUpstream(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def do_POST(self):
        ln = int(self.headers.get('content-length') or 0)
        body = json.loads(self.rfile.read(ln) if ln else b'{}')
        FakeUpstream.last = body
        stream = body.get('stream')
        if body.get('force_status'):
            self.send_response(body['force_status'])
            self.send_header('Content-Length', '5')
            self.end_headers()
            self.wfile.write(b'boom!')
            return
        if stream:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"reasoning_content":"abc"}}]}\n\n')
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
            self.close_connection = True  # SSE 结束后关闭连接（与真实上游行为一致）
        else:
            out = json.dumps({'choices': [{'message': {'reasoning_content': 'abc', 'content': 'hi'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)


def post(payload):
    c = http.client.HTTPConnection('127.0.0.1', 18799, timeout=10)
    c.request('POST', '/v1/chat/completions', body=json.dumps(payload).encode(),
              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer test'})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def main():
    up = ThreadingHTTPServer(('127.0.0.1', UPSTREAM_PORT), FakeUpstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    rewrite_proxy.UPSTREAM_TABLE = {'fake-model': 'http://127.0.0.1:%d' % UPSTREAM_PORT}
    psrv = ThreadingHTTPServer(('127.0.0.1', 18799), rewrite_proxy.Handler)
    threading.Thread(target=psrv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    SUFFIX = rewrite_proxy.DEFAULT_SUFFIX

    # 1) 流式 + 约束拼接
    r, data = post({'model': 'fake-model', 'stream': True,
                    'messages': [{'role': 'user', 'content': 'hello'}]})
    sent = FakeUpstream.last
    check('stream: status 200', r.status == 200, r.status)
    check('stream: SSE content-type', 'event-stream' in r.getheader('Content-Type', ''))
    check('stream: suffix appended to last user msg',
          sent['messages'][-1]['content'] == 'hello' + SUFFIX)
    check('stream: passthrough contains reasoning+DONE',
          b'reasoning_content' in data and b'[DONE]' in data)

    # 2) 非流式
    r, data = post({'model': 'fake-model', 'stream': False,
                    'messages': [{'role': 'user', 'content': 'q'}]})
    check('non-stream: status 200', r.status == 200)
    check('non-stream: JSON content-type', 'application/json' in r.getheader('Content-Type', ''))
    obj = json.loads(data)
    check('non-stream: message intact', obj['choices'][0]['message']['content'] == 'hi')
    check('non-stream: suffix appended', FakeUpstream.last['messages'][-1]['content'].endswith(SUFFIX))

    # 3) 上游错误状态码透传
    r, data = post({'model': 'fake-model', 'stream': True, 'force_status': 429,
                    'messages': [{'role': 'user', 'content': 'x'}]})
    check('upstream 429 passthrough', r.status == 429, r.status)

    # 4) 多模态数组 content：拼进最后一个 text part
    r, data = post({'model': 'fake-model', 'stream': False,
                    'messages': [{'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url': 'http://x/img.png'}},
                        {'type': 'text', 'text': 'describe'}]}]})
    sent_c = FakeUpstream.last['messages'][-1]['content']
    check('multimodal: text part got suffix', sent_c[-1]['text'].endswith(SUFFIX))
    check('multimodal: image part untouched', sent_c[0]['type'] == 'image_url')

    # 5) 无路由 → 502
    r, data = post({'model': 'no-such-model', 'stream': True,
                    'messages': [{'role': 'user', 'content': 'x'}]})
    check('no route: 502', r.status == 502, r.status)

    up.shutdown()
    psrv.shutdown()
    bad = [x for x in results if not x[1]]
    print('\n%d/%d passed' % (len(results) - len(bad), len(results)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()

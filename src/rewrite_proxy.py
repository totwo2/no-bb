#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rewrite_proxy.py —— No BB · 收敛约束中间层
====================================================
在大模型 API 和应用之间插一层：对话消息先进中间层，自动把收敛约束拼到
最后一个 user 消息尾部，再转发给真实上游。

特性：
  用户无感（约束由中间层自动拼接，无需每句话手动带）
  模型无关（按 upstreams.json 路由，换模型不改代码）
  零 key（客户端鉴权头原样透传，中间层不持有凭据）
  fail-open（任何解析失败都原样透传，不拦请求）
  响应零修改（上游返回的 reasoning/content 原样回传，一个字不动）

行为：
  POST /v1/chat/completions
    → 读 body
    → 在最后一个 user 消息的 content 尾部拼 REWRITE_SUFFIX（可配置）
    → 按 model 路由真实上游（upstreams.json + UPSTREAM env 兜底）
    → 流式请求：SSE 边收边转；非流式请求：整包透传
    → 上游错误状态码（401/429/500 等）原样透传给客户端
    → 记录改写日志 data/rewrite_events.jsonl（仅长度统计，不含对话内容）

用法：
  python rewrite_proxy.py                                 # 默认端口 18772
  REWRITE_SUFFIX_FILE=... python rewrite_proxy.py         # 约束词从文件读
  客户端把 base_url 指向 http://127.0.0.1:18772/v1 即可
"""
import os, json, time, threading, http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('PORT', '18772'))
LOGDIR = os.environ.get('LOGDIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data'))
HERE = os.path.dirname(os.path.abspath(__file__))

# 默认收敛约束词（4 候选约束 × 4 档思维强度全矩阵评测选出的最优版，见 README）。
# 注意：约束遵从是概率性的（同题单次结果分布较宽），精确计算场景可换弱化版约束词。
DEFAULT_SUFFIX = (
    "\n\n（附加指令：请尽量精炼思考。一旦得出最终结论，立即停止继续推演，"
    "不要复述推理过程、不要自我检查、不要补充额外说明，直接输出最终答案。）"
)

# 可选：从文件读约束（改措辞不动代码）
SUFFIX_FILE = os.environ.get('REWRITE_SUFFIX_FILE', '')
if SUFFIX_FILE and os.path.exists(SUFFIX_FILE):
    with open(SUFFIX_FILE, encoding='utf-8') as _f:
        DEFAULT_SUFFIX = _f.read().strip()

# 开关：REWRITE_ENABLED=0 时纯透传不拼约束（A/B 对照用）
ENABLED = os.environ.get('REWRITE_ENABLED', '1') == '1'

# model → 真实上游 base 路由表
UPSTREAMS_FILE = os.path.join(HERE, 'upstreams.json')
try:
    with open(UPSTREAMS_FILE, encoding='utf-8') as _f:
        UPSTREAM_TABLE = json.load(_f)
except Exception:
    UPSTREAM_TABLE = {}

_lock = threading.Lock()


def log_event(obj):
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        with _lock:
            with open(os.path.join(LOGDIR, 'rewrite_events.jsonl'), 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(obj, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _split_url(up):
    up = up.rstrip('/')
    if up.startswith('http://'):
        host, use_tls, prefix = up[7:], False, ''
    elif up.startswith('https://'):
        host, use_tls, prefix = up[8:], True, ''
    else:
        host, use_tls, prefix = up, False, ''
    if '/' in host:
        host, prefix = host.split('/', 1)
        prefix = '/' + prefix
    return host, use_tls, prefix


def _dump(rb):
    return json.dumps(rb, ensure_ascii=False).encode('utf-8')


def rewrite_body(body):
    """在最后一个 user 消息 content 尾部拼约束。返回 (新 body, 是否改写, 原长, 新长)。

    content 为字符串：直接拼。
    content 为数组（多模态）：拼进最后一个 type=text 的 part；纯图片等
    无文本部分的消息不拼（原样透传）。
    解析失败一律原样返回（fail-open）。
    """
    try:
        rb = json.loads(body or b'{}')
    except Exception:
        return body, False, 0, 0
    msgs = rb.get('messages') or []
    if not isinstance(msgs, list) or not msgs:
        return body, False, 0, 0
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if not (isinstance(m, dict) and m.get('role') == 'user'):
            continue
        c = m.get('content')
        if isinstance(c, str):
            m['content'] = c + DEFAULT_SUFFIX
            return _dump(rb), True, len(c), len(c) + len(DEFAULT_SUFFIX)
        if isinstance(c, list):
            for part in reversed(c):
                if (isinstance(part, dict) and part.get('type') == 'text'
                        and isinstance(part.get('text'), str)):
                    orig = part['text']
                    part['text'] = orig + DEFAULT_SUFFIX
                    return _dump(rb), True, len(orig), len(orig) + len(DEFAULT_SUFFIX)
            return body, False, 0, 0
    return body, False, 0, 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _reply_simple(self, status, payload):
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get('content-length') or 0)
        body = self.rfile.read(length) if length else b'{}'
        t0 = time.time()

        try:
            rb = json.loads(body or b'{}')
        except Exception:
            rb = {}
        model = rb.get('model') or '?'
        mid = model if isinstance(model, str) else '?'
        want_stream = bool(rb.get('stream'))
        base_id = mid[:-3] if mid.endswith('.il') else mid  # 兼容带 .il 后缀的模型别名
        upstream_base = UPSTREAM_TABLE.get(base_id) or os.environ.get('UPSTREAM', '')
        if not upstream_base:
            log_event(dict(ts=time.time(), model=model, ok=False,
                           reason='no_upstream_route', elapsed_ms=0))
            self._reply_simple(502, b'no upstream route for model')
            return

        new_body, rewrote, orig_len, new_len = rewrite_body(body) if ENABLED else (body, False, 0, 0)
        send_body = new_body

        # 转发上游（鉴权头透传，中间层不持有凭据）
        host, use_tls, prefix = _split_url(upstream_base)
        conn = (http.client.HTTPSConnection(host, timeout=300) if use_tls
                else http.client.HTTPConnection(host, timeout=300))
        hdr = {'Content-Type': 'application/json'}
        lk = {k.lower(): v for k, v in dict(self.headers).items()}
        if 'authorization' in lk:
            hdr['Authorization'] = lk['authorization']
        for k in ('api-key', 'x-api-key'):
            if k in lk:
                hdr[k] = lk[k]

        reasoning_chars = 0
        answer_chars = 0
        got_done = False
        err = ''
        upstream_status = 0
        try:
            conn.request('POST', prefix + '/v1/chat/completions', body=send_body, headers=hdr)
            resp = conn.getresponse()
            upstream_status = resp.status

            # 上游错误状态码（401/429/500…）原样透传，不吞、不伪装成 200
            if upstream_status != 200:
                err_body = resp.read()
                self.send_response(upstream_status)
                self.send_header('Content-Type',
                                 resp.getheader('Content-Type') or 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
                log_event(dict(ts=time.time(), model=model, ok=False, rewrote=rewrote,
                               upstream_status=upstream_status,
                               err=err_body[:200].decode('utf-8', 'replace'),
                               elapsed_ms=int((time.time() - t0) * 1000)))
                return

            if want_stream:
                # 流式：SSE 边收边转（内容零修改，只统计不改动）
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True  # SSE 无长度语义，结束时关连接让客户端收到 EOF
                buf = b''
                while True:
                    chunk = resp.read1(8192)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except Exception:
                        break
                    buf += chunk
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        line = line.strip()
                        if not line.startswith(b'data:'):
                            continue
                        payload = line[5:].strip()
                        if payload == b'[DONE]':
                            got_done = True
                            break
                        try:
                            obj = json.loads(payload)
                        except Exception:
                            continue
                        d = ((obj.get('choices') or [{}])[0].get('delta')) or {}
                        rt = d.get('reasoning_content') or d.get('reasoning')
                        if rt:
                            reasoning_chars += len(rt)
                        elif d.get('content'):
                            answer_chars += len(d['content'])
                    if got_done:
                        break
            else:
                # 非流式：整包读完后按 JSON 透传
                data = resp.read()
                try:
                    obj = json.loads(data)
                    ch = (obj.get('choices') or [{}])[0]
                    msg = ch.get('message') or {}
                    rt = msg.get('reasoning_content') or msg.get('reasoning')
                    if rt:
                        reasoning_chars = len(rt)
                    if msg.get('content'):
                        answer_chars = len(msg['content'])
                except Exception:
                    pass
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except Exception as ex:
            err = '%s: %s' % (type(ex).__name__, ex)
            try:
                self._reply_simple(502, ('upstream error: %s' % err).encode('utf-8'))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

        log_event(dict(ts=time.time(), model=model, ok=(not err), rewrote=rewrote,
                       upstream_status=upstream_status,
                       orig_len=orig_len, new_len=new_len,
                       reasoning_chars=reasoning_chars, answer_chars=answer_chars,
                       got_done=got_done, err=err,
                       elapsed_ms=int((time.time() - t0) * 1000)))


def serve():
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print('rewrite_proxy :%d  enabled=%s  suffix_len=%d  routes=%s'
          % (PORT, ENABLED, len(DEFAULT_SUFFIX), sorted(UPSTREAM_TABLE.keys())), flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    serve()

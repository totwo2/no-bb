#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长链高耗题集评测（唯一标准答案版）
============================================================
为什么要有这份题集（2026-09-06）：
  GSM8K 风格短题上，现代推理模型会「自动少想」——基线 reasoning 仅 100~550 字符，
  再怎么压也压不出多少绝对值，说「省 67%」没有说服力。
  真正的痛点在开放式长链推演（原 finance/scheduling 类题，基线 24k~45k 字符）。
  但 finance 题有双口径歧义（成本按额增长 vs 按率上浮），判不了分。
  ⇒ 本集设计原则：**每题唯一确定数值答案 + 强制多步建表推演**，
    既逼出长链，又能 ExactMatch 判分，绕开歧义。

答案由独立脚本复算锁定（见 verify_long_answers.py，与题目文本解耦）：
  L1 五年财务测算 = 1632.48 | L2 关键路径 = 15 | L3 容斥计数 = 200
  L4 折扣税费链 = 3375     | L5 流水车间排程 = 19

矩阵：2 模型 × {C0 无约束基线, C1 中间层默认约束} × 5 题 × 3 次重复，effort=high
      （3 次重复用于对抗「约束遵从概率性」——单样本曾出现 3.2k~28.8k 宽分布）
用法: python bench_long.py [--models step-3.7-flash,agnes-2.5-flash] [--reps 3] [--jobs 6] [--smoke]
"""
import os, re, sys, json, time, http.client, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAMS_JSON = os.path.join(HERE, '..', 'src', 'upstreams.json')
OUT = os.path.join(HERE, '..', 'results')
os.makedirs(OUT, exist_ok=True)

EFFORT = 'high'

# ============ 长链高耗题集（每题唯一确定数值答案） ============
# 设计约束：① 必须建表/多步推演，逼出长思考链；② 所有假设在题面写死，不留解释空间。
LONG_PROBLEMS = [
    {"id": "L1", "answer": "1632.48",
     "q": "某公司规划未来 5 年（Y1 至 Y5）的经营情况，各项假设如下，请逐年计算后汇总：\n"
          "（1）营业收入：Y1 = 1200 万元，此后每年比上年增长 25%；\n"
          "（2）营业成本：每年等于当年营业收入的 62%；\n"
          "（3）销售费用：Y1 = 150 万元，此后每年比上年增长 10%；\n"
          "（4）管理费用：每年固定 80 万元；\n"
          "（5）折旧摊销：每年固定 50 万元；\n"
          "（6）营业利润 = 营业收入 − 营业成本 − 销售费用 − 管理费用 − 折旧摊销；\n"
          "（7）所得税 = 营业利润 × 25%，净利润 = 营业利润 − 所得税。\n"
          "请给出 Y1 到 Y5 每一年的营业收入、营业成本、销售费用、营业利润、净利润，"
          "并求这五年净利润的合计数（万元，四舍五入保留两位小数）。"},
    {"id": "L2", "answer": "15",
     "q": "某工程包含 7 个任务，工期（天）与前置依赖如下：\n"
          "A 需 3 天，无前置；\n"
          "B 需 4 天，必须在 A 完成后开始；\n"
          "C 需 2 天，必须在 A 完成后开始；\n"
          "D 需 5 天，必须在 B 完成后开始；\n"
          "E 需 3 天，必须在 C 完成后开始；\n"
          "F 需 2 天，必须在 D 和 E 都完成后开始；\n"
          "G 需 1 天，必须在 F 完成后开始。\n"
          "可并行的任务可同时进行，人力物力不限。请列出每个任务的最早开始与最早完成时间，"
          "指出关键路径，并求完成整个工程所需的最短总工期（天，整数）。"},
    {"id": "L3", "answer": "200",
     "q": "在 1 到 500（含 1 和 500）的全部整数中，"
          "满足「能被 3 整除或能被 5 整除，但不能被 7 整除」的数一共有多少个？\n"
          "请先用容斥原理分步计算（分别列出被 3 整除的个数、被 5 整除的个数、被 15 整除的个数、"
          "被 21 整除的个数、被 35 整除的个数、被 105 整除的个数），再给出最终个数（整数）。"},
    {"id": "L4", "answer": "3375",
     "q": "某商品标价 5000 元，结算时严格按以下顺序依次执行每一步（后一步基于前一步的结果）：\n"
          "第 1 步：全场折扣，打 85 折；\n"
          "第 2 步：满减，每满 2000 元减 300 元（按第 1 步结束后的金额计算，"
          "不足 2000 元的部分不减，例如 4250 元满足 2 个 2000 元，减 600 元）；\n"
          "第 3 步：店铺券，第 2 步结束后的金额若达到 3000 元则减 400 元，否则不减；\n"
          "第 4 步：运费 60 元（单独加收，不参与前面任何折扣或满减）；\n"
          "第 5 步：平台服务费，按第 3 步结束后的商品金额（不含运费）的 2% 收取。\n"
          "请逐步列出每一步之后的金额，并求买家最终实付总额（元，精确到元）。"},
    {"id": "L5", "answer": "19",
     "q": "车间有 3 台机器 M1、M2、M3 和 6 个订单 J1 至 J6。"
          "每个订单必须先经 M1 加工、再经 M2 加工、最后经 M3 加工（同一订单的三道工序不能并行）；"
          "不同订单可以在不同机器上同时加工；每台机器同一时刻只能加工一个订单；"
          "每台机器都严格按照 J1、J2、J3、J4、J5、J6 的顺序加工。"
          "各订单在每台机器上的加工工时（小时）如下：\n"
          "J1：M1=2，M2=3，M3=1\n"
          "J2：M1=1，M2=2，M3=4\n"
          "J3：M1=3，M2=1，M3=2\n"
          "J4：M1=2，M2=4，M3=3\n"
          "J5：M1=4，M2=2，M3=1\n"
          "J6：M1=1，M2=3，M3=2\n"
          "请推算出每个订单在每台机器上的开始与结束时刻，并求全部订单加工完成所需的最短总时长"
          "（小时，整数）。"},
]

SUFFIXES = {
    "C0_base": "",
    "C1_weak": "\n\n（附加指令：请尽量精炼思考。一旦得出最终结论，立即停止继续推演，"
               "不要复述推理过程、不要自我检查、不要补充额外说明，直接输出最终答案。）",
}

ANSWER_TAIL = "\n最后请单独用一行输出答案，格式：【答案】数字（不要加单位或文字）。"


def model_conf(mid):
    """凭据来源：env LLM_API_BASE/LLM_API_KEY 优先，否则查 src/upstreams.json 路由表。"""
    base = os.environ.get('LLM_API_BASE', '')
    key = os.environ.get('LLM_API_KEY', '')
    if not base:
        try:
            base = json.load(open(UPSTREAMS_JSON, encoding='utf-8')).get(mid, '')
        except Exception:
            base = ''
    if not base:
        sys.exit('no API base for %s: set LLM_API_BASE (and LLM_API_KEY)' % mid)
    return {'url': base, 'apiKey': key}


def split_base(url):
    url = url.rstrip('/')
    scheme = 'https://' if url.startswith('https://') else 'http://'
    rest = url[len(scheme):]
    host, _, path = rest.partition('/')
    return host, (scheme == 'https://'), '/' + path


def call_once(mid, problem_text, suffix, timeout=600):
    m = model_conf(mid)
    host, tls, prefix = split_base(m['url'])
    content = problem_text + ANSWER_TAIL + suffix
    bd = {'model': mid, 'stream': True,
          'messages': [{'role': 'user', 'content': content}],
          'reasoning_effort': EFFORT}
    body = json.dumps(bd, ensure_ascii=False).encode('utf-8')
    conn = (http.client.HTTPSConnection(host, timeout=timeout) if tls
            else http.client.HTTPConnection(host, timeout=timeout))
    t0 = time.time()
    reasoning, answer, done, err = '', '', False, ''
    try:
        conn.request('POST', prefix + '/chat/completions', body=body,
                     headers={'Content-Type': 'application/json',
                              'Authorization': 'Bearer ' + m.get('apiKey', '')})
        resp = conn.getresponse()
        if resp.status != 200:
            err = 'http_%d: %s' % (resp.status, resp.read(500).decode('utf-8', 'replace'))
            return dict(reasoning='', answer='', wall=time.time() - t0, done=False, err=err)
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
                payload = line[5:].strip()
                if payload == b'[DONE]':
                    done = True
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                d = ((obj.get('choices') or [{}])[0].get('delta')) or {}
                rt = d.get('reasoning_content') or d.get('reasoning')
                if rt:
                    reasoning += rt
                elif d.get('content'):
                    answer += d['content']
            if done:
                break
    except Exception as ex:
        err = '%s: %s' % (type(ex).__name__, ex)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return dict(reasoning=reasoning, answer=answer, wall=time.time() - t0, done=done, err=err)


def extract_answer(answer_text):
    if not answer_text:
        return None
    mm = re.search(r'【答案】\s*([-0-9][0-9,\.]*)', answer_text)
    if mm:
        return _clean(mm.group(1))
    for line in reversed(answer_text.strip().splitlines()):
        nums = re.findall(r'[-+]?\d[\d,]*(?:\.\d+)?', line)
        if nums:
            return _clean(nums[-1])
    return None


def _clean(s):
    s = s.replace(',', '').replace('，', '').strip().rstrip('。')
    try:
        return float(s)
    except Exception:
        return None


def judge(gold, got):
    """ExactMatch 判分。

    容差 = max(绝对 0.01, 相对 0.01%)。
    为什么要放宽：L1 标准答案 1632.48（两位小数），模型答 1632.5 属合法四舍五入，
    绝对容差 0.01 会把它误判为错（实测捕获到 2 例）。相对项 0.01% 对 1632.48 仅 ±0.16，
    仍严到足以拦住真错（实测 agnes 的 1631.42 / 1652.47 依旧判错）。
    """
    if got is None:
        return False
    try:
        g, x = float(gold), float(got)
    except Exception:
        return False
    return abs(g - x) < max(0.01, abs(g) * 0.0001)


def work_item(mid, suffix_key, prob, rep):
    r = call_once(mid, prob['q'], SUFFIXES[suffix_key])
    got = extract_answer(r['answer'])
    ok = judge(prob['answer'], got)
    return dict(pid=prob['id'], gold=prob['answer'], got=got, ok=ok, rep=rep,
                rlen=len(r['reasoning']), alen=len(r['answer']),
                wall=round(r['wall'], 1), err=r['err'], model=mid, suffix=suffix_key)


def aggregate(model, suffix, rows):
    ok_n = sum(1 for x in rows if x['ok'])
    rlens = sorted(x['rlen'] for x in rows if not x['err'])
    walls = sorted(x['wall'] for x in rows if not x['err'])
    n = len(rows)
    return dict(model=model, suffix=suffix, effort='E3_high', set='long', n=n,
                correct=ok_n, accuracy=round(ok_n / n, 3) if n else 0,
                rlen_median=int(rlens[len(rlens) // 2]) if rlens else 0,
                rlen_mean=round(sum(rlens) / len(rlens), 1) if rlens else 0,
                wall_median=round(walls[len(walls) // 2], 1) if walls else 0,
                wall_mean=round(sum(walls) / len(walls), 1) if walls else 0,
                rows=[{k: x[k] for k in ('pid', 'rep', 'gold', 'got', 'ok', 'rlen', 'alen', 'wall', 'err')}
                      for x in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--models', default='step-3.7-flash,agnes-2.5-flash')
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--pids', default='', help='只跑指定题号，逗号分隔，如 L1,L3（串行计时实验用）')
    ap.add_argument('--smoke', action='store_true')
    a = ap.parse_args()
    models = [m.strip() for m in a.models.split(',') if m.strip()]

    probs = LONG_PROBLEMS[:1] if a.smoke else LONG_PROBLEMS
    if a.pids:
        want = {s.strip().upper() for s in a.pids.split(',') if s.strip()}
        probs = [p for p in probs if p['id'] in want]
        if not probs:
            sys.exit('--pids 未匹配任何题号: ' + a.pids)
    tasks = [(mid, sk, p, rep)
             for mid in models for sk in ('C0_base', 'C1_weak')
             for p in probs for rep in range(1, a.reps + 1)]

    ts = time.strftime('%Y%m%d_%H%M%S')
    logp = os.path.join(OUT, 'bench_long_%s.log' % ts)
    log_fh = open(logp, 'w', encoding='utf-8')
    print('长链评测: %d 次调用, jobs=%d, effort=%s, reps=%d, 模型=%s'
          % (len(tasks), a.jobs, EFFORT, a.reps, models), flush=True)

    done_rows = []
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(work_item, mid, sk, p, rep): (mid, sk, p['id'], rep)
                for (mid, sk, p, rep) in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            mid, sk, pid, rep = futs[fut]
            row = fut.result()
            line = ('[%d/%d] %s/%s/%s#%d ok=%s got=%s gold=%s rlen=%d wall=%.1fs%s'
                    % (i, len(tasks), mid, sk, pid, rep, row['ok'], row['got'], row['gold'],
                       row['rlen'], row['wall'], (' ERR:' + row['err'][:100]) if row['err'] else ''))
            print(line, flush=True)
            log_fh.write(line + '\n')
            log_fh.flush()
            done_rows.append(row)

    stats = []
    for mid in models:
        for sk in ('C0_base', 'C1_weak'):
            rows_m = [r for r in done_rows if r['model'] == mid and r['suffix'] == sk]
            for pid in [p['id'] for p in probs]:
                rows_p = [r for r in rows_m if r['pid'] == pid]
                if rows_p:
                    st = aggregate(mid, sk, rows_p)
                    st['pid'] = pid
                    stats.append(st)
            # 题集整体
            if rows_m:
                st = aggregate(mid, sk, rows_m)
                st['pid'] = 'ALL'
                stats.append(st)
                print('STAT %s %s ALL: acc=%s correct=%d/%d rlen_med=%d rlen_mean=%.0f wall_med=%.1fs'
                      % (mid, sk, st['accuracy'], st['correct'], st['n'],
                         st['rlen_median'], st['rlen_mean'], st['wall_median']), flush=True)

    out_json = os.path.join(OUT, 'bench_long_%s.json' % ts)
    with open(out_json, 'w', encoding='utf-8') as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=1)
    print('saved ->', out_json, flush=True)
    log_fh.close()


if __name__ == '__main__':
    main()

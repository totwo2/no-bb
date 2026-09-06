#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No BB · 收敛约束 · 高思考强度评测（并发版）
============================================================
背景：effort 档位由客户端产品层决定，中间层无法知晓也无法指导用户选档
→ 「选最优 effort」是死胡同。
新基准 = 最坏情况：固定 reasoning_effort=high（用户开深度思考、模型火力全开），
在此条件下测 4 个约束词是否仍收敛、正确率是否掉、省多少 token/时间。

凭据来源：环境变量 LLM_API_BASE / LLM_API_KEY（LLM_API_BASE 缺省时
从 src/upstreams.json 按模型名查路由）。

矩阵：2 模型 × 4 约束词 × 13 题（8 GSM8K 风格 + 5 长链难题），全部并发。
判定：ExactMatch（同 bench_matrix）。输出 flat stat 列表（按题集 n=8/n=5 分条）。

用法: python bench_high.py [--models <id1>,<id2>] [--jobs 6] [--smoke]
"""
import os, re, sys, json, time, http.client, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAMS_JSON = os.path.join(HERE, '..', 'src', 'upstreams.json')
OUT = os.path.join(HERE, '..', 'results')
os.makedirs(OUT, exist_ok=True)

EFFORT = 'high'  # 固定高思考强度（最坏情况）

# ============ 题集（与 bench_matrix 相同，答案唯一整数） ============
PROBLEMS = [
    {"id": "p1", "answer": "504",
     "q": "商店进了 240 个杯子，每个成本 3.5 元，计划每个卖 6 元。先按原价卖出了三分之二，"
          "剩下的打八折全部卖完。请问商店这批杯子的总利润是多少元？"},
    {"id": "p2", "answer": "60",
     "q": "一个三层书架一共放了 270 本书。上层书数量是中层的 2 倍，下层比中层多 30 本。"
          "请问中层放了多少本书？"},
    {"id": "p3", "answer": "6",
     "q": "一项工程，甲队单独做需要 10 天完成，乙队单独做需要 15 天完成。如果两队合作，"
          "需要多少天完成这项工程？"},
    {"id": "p4", "answer": "10816",
     "q": "小明把 10000 元存入银行，年利率 4%，按复利计算（每年利息并入本金）。"
          "请问 2 年后他一共可以取回多少元？（只算整数部分）"},
    {"id": "p5", "answer": "12",
     "q": "修一条路，甲工程队单独修要 20 天，乙工程队单独修要 30 天。"
          "两队同时从两端开始修，多少天可以修完？"},
    {"id": "p6", "answer": "160",
     "q": "花店玫瑰每支 5 元，促销活动是买 8 支送 2 支。小王需要 40 支玫瑰，"
          "请问他最少要付多少钱？"},
    {"id": "p7", "answer": "6000",
     "q": "一个家庭月收入 12000 元。房租占收入的四分之一，伙食费占剩余部分的三分之一，"
          "其余全部存起来。请问这个家庭每月存多少钱？"},
    {"id": "p8", "answer": "36",
     "q": "一个果园里苹果树的数量是梨树的 3 倍，梨树比桃树多 20 棵，三种树一共 260 棵。"
          "请问桃树有多少棵？"},
]
HARD_PROBLEMS = [
    {"id": "h1", "answer": "350",
     "q": "某工厂生产玩具，第 1 个月生产 3000 个，之后每个月产量比上个月增加 200 个，连续生产 6 个月。"
          "每 5 个玩具装一盒，每 12 盒装一箱。请问这 6 个月生产的玩具一共能装满多少整箱？"},
    {"id": "h2", "answer": "366",
     "q": "一个三位数的各位数字之和是 15。它的个位数字比百位数字大 3，十位数字是百位数字的 2 倍。"
          "这个三位数是多少？"},
    {"id": "h3", "answer": "4500",
     "q": "某商品按定价打八折出售仍能获利 20%（利润率 = 利润 ÷ 成本）。若该商品的成本是 3000 元，"
          "则该商品的定价是多少元？"},
    {"id": "h4", "answer": "3",
     "q": "一项工程，甲队单独做需 12 天完成，乙队单独做需 18 天完成，丙队单独做需 36 天完成。"
          "甲队先单独做 6 天，然后甲、乙、丙三队一起做，问还需多少天完成？"},
    {"id": "h5", "answer": "10",
     "q": "学校买了一批笔记本分给各班。如果每班分 12 本，会多出 6 本；如果每班分 15 本，会少 24 本。"
          "请问学校有多少个班？"},
]

# ============ 约束词档位（4 档，与 bench_matrix 相同） ============
SUFFIXES = {
    "C0_base": "",            # 无约束基线
    "C1_weak": "\n\n（附加指令：请尽量精炼思考。一旦得出最终结论，立即停止继续推演，"
               "不要复述推理过程、不要自我检查、不要补充额外说明，直接输出最终答案。）",
    "C2_orig": "\n\n请在有答案后直接返回结果。",
    "C3_strong": "\n\n（重要指令，最高优先级：一旦推理得出最终答案，立即停止思考并直接返回结果。"
                 "禁止复述推理过程，禁止自我检查，禁止补充任何额外说明。直接输出最终答案。）",
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


def call_once(mid, problem_text, suffix, timeout=300):
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
    mm = re.search(r'【答案】\s*([-0-9][0-9,\.]*|[-+]?\d+[\.,]?\d*)', answer_text)
    if mm:
        return _clean(mm.group(1))
    for line in reversed(answer_text.strip().splitlines()):
        nums = re.findall(r'[-+]?\d[\d,]*(?:\.\d+)?', line)
        if nums:
            return _clean(nums[-1])
    return None


def _clean(s):
    s = s.replace(',', '').replace('，', '').strip()
    try:
        return float(s)
    except Exception:
        return None


def judge(gold, got):
    if got is None:
        return False
    try:
        return abs(float(gold) - float(got)) < 0.01
    except Exception:
        return False


def work_item(mid, suffix_key, prob):
    """单格 (模型,约束,单题) → 行 dict。"""
    r = call_once(mid, prob['q'], SUFFIXES[suffix_key])
    got = extract_answer(r['answer'])
    ok = judge(prob['answer'], got)
    return dict(pid=prob['id'], gold=prob['answer'], got=got, ok=ok,
                rlen=len(r['reasoning']), alen=len(r['answer']),
                wall=round(r['wall'], 1), err=r['err'],
                model=mid, suffix=suffix_key)


def aggregate(model, suffix, rows):
    ok_n = sum(1 for x in rows if x['ok'])
    rlens = sorted(x['rlen'] for x in rows if not x['err'])
    walls = sorted(x['wall'] for x in rows if not x['err'])
    return dict(model=model, suffix=suffix, effort='E3_high', n=len(rows),
                correct=ok_n, accuracy=round(ok_n / len(rows), 3),
                rlen_median=int(rlens[len(rlens) // 2]) if rlens else 0,
                wall_median=round(walls[len(walls) // 2], 1) if walls else 0,
                rows=[{k: x[k] for k in ('pid', 'gold', 'got', 'ok', 'rlen', 'alen', 'wall', 'err')} for x in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--models', default='step-3.7-flash,agnes-2.5-flash')
    ap.add_argument('--jobs', type=int, default=6, help='并发数')
    ap.add_argument('--smoke', action='store_true', help='每格只跑 1 题冒烟')
    a = ap.parse_args()
    models = [m.strip() for m in a.models.split(',') if m.strip()]

    # 摊平任务：每个 (model, suffix) 一个题集切片
    tasks = []  # (model, suffix_key, 属于哪个题集, prob)
    for mid in models:
        for sk in ('C0_base', 'C1_weak', 'C2_orig', 'C3_strong'):
            ps = (PROBLEMS + HARD_PROBLEMS) if not a.smoke else PROBLEMS[:1]
            for prob in ps:
                tasks.append((mid, sk, prob))

    ts = time.strftime('%Y%m%d_%H%M%S')
    logp = os.path.join(OUT, 'bench_high_%s.log' % ts)
    log_fh = open(logp, 'w', encoding='utf-8')
    print('并发评测: %d 格, jobs=%d, effort=%s, 模型=%s' % (len(tasks), a.jobs, EFFORT, models), flush=True)

    done_rows = []  # 全部行 dict（含 model/suffix）
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(work_item, mid, sk, prob): (mid, sk, prob['id'])
                for (mid, sk, prob) in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            mid, sk, pid = futs[fut]
            row = fut.result()
            line = ('  [%s/%s/%s] ok=%s got=%s gold=%s rlen=%d wall=%.1fs%s'
                    % (mid, sk, pid, row['ok'], row['got'], row['gold'],
                       row['rlen'], row['wall'], (' ERR:' + row['err'][:100]) if row['err'] else ''))
            print(line, flush=True)
            log_fh.write(line + '\n')
            log_fh.flush()
            done_rows.append(row)

    # 按 (model, suffix, 题集) 聚合 → flat stat 列表
    stats = []
    for mid in models:
        for sk in ('C0_base', 'C1_weak', 'C2_orig', 'C3_strong'):
            rows_m = [r for r in done_rows if r['model'] == mid and r['suffix'] == sk]
            for label, keys, n in (('gsm8k', [p['id'] for p in PROBLEMS], len(PROBLEMS)),
                                   ('hard', [p['id'] for p in HARD_PROBLEMS], len(HARD_PROBLEMS))):
                rows_s = [r for r in rows_m if r['pid'] in keys]
                if not rows_s:
                    continue
                st = aggregate(mid, sk, rows_s)
                st['set'] = label
                stats.append(st)
                print('STAT %s %s %s: acc=%s correct=%d/%d rlen_med=%d wall_med=%.1fs'
                      % (mid, sk, label, st['accuracy'], st['correct'], st['n'],
                         st['rlen_median'], st['wall_median']), flush=True)
                log_fh.write('STAT %s %s %s: acc=%s correct=%d/%d rlen_med=%d wall_med=%.1fs\n'
                             % (mid, sk, label, st['accuracy'], st['correct'], st['n'],
                                st['rlen_median'], st['wall_median']))

    out_json = os.path.join(OUT, 'bench_high_%s.json' % ts)
    with open(out_json, 'w', encoding='utf-8') as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=1)
    print('saved ->', out_json, flush=True)
    log_fh.close()


if __name__ == '__main__':
    main()

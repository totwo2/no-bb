#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No BB · 收敛约束全矩阵评测（GSM8K 风格算术应用题，答案唯一，ExactMatch 判分）
============================================================
目标：用一份标准推导题集，把待测模型用各档约束词 × 各档思维强度全测，
选出最优约束词，供中间层默认配置使用（正确率 + 省时 + 省 token 百分比）。

凭据来源：环境变量 LLM_API_BASE / LLM_API_KEY（LLM_API_BASE 缺省时
从 src/upstreams.json 按模型名查路由）。

业界基准参照：GSM8K（小学数学多步算术，ExactMatch 判最终数字）为推理评测标配；
本测试自建 8 道 GSM8K 风格应用题（答案唯一整数，避免口径歧义），模型输出
【答案】行由判分器提取数字 ExactMatch（容差 0.01）。

矩阵（两阶段收敛控制调用量）：
  阶段1 选约束词（default 强度）：2 模型 × {C0基线, C1弱版, C2极简, C3强版} × 8 题 = 64 调
  阶段2 选强度（最优约束 C*）：2 模型 × {C0基线, C*} × {low, medium, high} × 8 题
判定：正确率(8题答对数) 不能掉 vs 基线；reasoning 字符中位节省% / 墙钟节省%。
用法: python bench_matrix.py --phase 1|2 --model <model-id> [--suffix KEY] [--effort E] [--smoke]
"""
import os, re, sys, json, time, http.client, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAMS_JSON = os.path.join(HERE, '..', 'src', 'upstreams.json')
OUT = os.path.join(HERE, '..', 'results')
os.makedirs(OUT, exist_ok=True)

# ============ GSM8K 风格标准题集（自建，答案唯一整数） ============
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

# ============ 长链难题集（口径唯一、需多步推演，测约束真实压缩收益与正确率风险） ============
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
ANSWER_TAIL_HARD = "\n最后请单独用一行输出答案，格式：【答案】数字（不要加单位或文字）。"

# 标准判分提示尾（要求模型单行输出答案数字，便于 ExactMatch）
ANSWER_TAIL = "\n最后请单独用一行输出答案，格式：【答案】数字（不要加单位或文字）。"

# ============ 约束词档位（阶段1 候选） ============
SUFFIXES = {
    "C0_base": "",            # 无约束基线
    "C1_weak": "\n\n（附加指令：请尽量精炼思考。一旦得出最终结论，立即停止继续推演，"
               "不要复述推理过程、不要自我检查、不要补充额外说明，直接输出最终答案。）",
    "C2_orig": "\n\n请在有答案后直接返回结果。",
    "C3_strong": "\n\n（重要指令，最高优先级：一旦推理得出最终答案，立即停止思考并直接返回结果。"
                 "禁止复述推理过程，禁止自我检查，禁止补充任何额外说明。直接输出最终答案。）",
}

EFFORTS = {"E0_default": None, "E1_low": "low", "E2_medium": "medium", "E3_high": "high"}

# ============ 模型路由 ============
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


def call_once(mid, problem_text, suffix, effort, timeout=240):
    """单题单次调用。返回 dict(reasoning, answer, wall, done, err)。"""
    m = model_conf(mid)
    host, tls, prefix = split_base(m['url'])
    content = problem_text + ANSWER_TAIL + suffix
    bd = {'model': mid, 'stream': True, 'messages': [{'role': 'user', 'content': content}]}
    if effort:
        bd['reasoning_effort'] = effort
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
    """从输出提取答案数字：优先【答案】后数字，其次最后一行数字。"""
    if not answer_text:
        return None
    # 1) 【答案】xxx
    mm = re.search(r'【答案】\s*([-0-9][0-9,\.]*|[-+]?\d+[\.,]?\d*)', answer_text)
    if mm:
        return _clean(mm.group(1))
    # 2) 末行找数字
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


def run_cell(mid, suffix_key, effort_key, problems, log_fh):
    """跑一组 (suffix, effort) 在全部题上。返回统计 dict。"""
    rows = []
    for p in problems:
        r = call_once(mid, p['q'], SUFFIXES[suffix_key], EFFORTS[effort_key])
        got = extract_answer(r['answer'])
        ok = judge(p['answer'], got)
        rows.append(dict(pid=p['id'], gold=p['answer'], got=got, ok=ok,
                         rlen=len(r['reasoning']), alen=len(r['answer']),
                         wall=round(r['wall'], 1), err=r['err']))
        line = ('  [%s/%s/%s] %s ok=%s got=%s gold=%s rlen=%d wall=%.1fs%s'
                % (mid, suffix_key, effort_key, p['id'], ok, got, p['answer'],
                   len(r['reasoning']), r['wall'], (' ERR:' + r['err'][:80]) if r['err'] else ''))
        print(line, flush=True)
        log_fh.write(line + '\n')
        log_fh.flush()
    ok_n = sum(1 for x in rows if x['ok'])
    rlens = sorted(x['rlen'] for x in rows if not x['err'])
    walls = sorted(x['wall'] for x in rows if not x['err'])
    stat = dict(model=mid, suffix=suffix_key, effort=effort_key,
                n=len(rows), correct=ok_n,
                accuracy=round(ok_n / len(rows), 3),
                rlen_median=int(rlens[len(rlens) // 2]) if rlens else 0,
                wall_median=round(walls[len(walls) // 2], 1) if walls else 0,
                rows=rows)
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', required=True, choices=['1', '2'])
    ap.add_argument('--model', required=True)
    ap.add_argument('--suffix', default='', help='phase2 用：C0_base 或最优 C*')
    ap.add_argument('--effort', default='', help='phase2 用：E1_low/E2_medium/E3_high')
    ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--hard', action='store_true', help='用长链难题集 HARD_PROBLEMS')
    ap.add_argument('--resume', default='', help='已跑结果 json 路径，增量合并')
    a = ap.parse_args()

    probs = (HARD_PROBLEMS[:2] if a.smoke else HARD_PROBLEMS) if a.hard \
        else (PROBLEMS[:2] if a.smoke else PROBLEMS)
    ts = time.strftime('%Y%m%d_%H%M%S')
    logp = os.path.join(OUT, 'bench_%s_%s_%s.log' % (a.model, a.phase, ts))
    log_fh = open(logp, 'w', encoding='utf-8')
    results = []

    if a.phase == '1':
        # 阶段1：default 强度下 4 个约束词
        for sk in ('C0_base', 'C1_weak', 'C2_orig', 'C3_strong'):
            st = run_cell(a.model, sk, 'E0_default', probs, log_fh)
            results.append(st)
            print('STAT %s %s: acc=%s correct=%d/%d rlen_med=%d wall_med=%.1fs'
                  % (a.model, sk, st['accuracy'], st['correct'], st['n'],
                     st['rlen_median'], st['wall_median']), flush=True)
    else:
        # 阶段2：给定 (suffix, effort) 单组（驱动脚本外层循环调用）
        assert a.suffix and a.effort, 'phase2 需 --suffix 和 --effort'
        st = run_cell(a.model, a.suffix, a.effort, probs, log_fh)
        results.append(st)
        print('STAT %s %s/%s: acc=%s correct=%d/%d rlen_med=%d wall_med=%.1fs'
              % (a.model, a.suffix, a.effort, st['accuracy'], st['correct'], st['n'],
                 st['rlen_median'], st['wall_median']), flush=True)

    out_json = os.path.join(OUT, 'bench_%s_%s_%s.json' % (a.model, a.phase, ts))
    with open(out_json, 'w', encoding='utf-8') as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print('saved ->', out_json, flush=True)
    log_fh.close()


if __name__ == '__main__':
    main()

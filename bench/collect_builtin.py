#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内置/代理通道模型指标采集器
============================================================
适用场景：被测模型不走本地 API、无法直连时（如平台内置模型），用「派子代理」
的方式跑评测：主会话给子代理下发禁用工具的纯推理任务，子代理的思考原文与
token 用量由平台落盘到会话 jsonl。本脚本从这些 jsonl 采集指标并聚合。

支持任意「会话落盘为 jsonl、含 reasoning 原文与 usage」的 Agent 运行环境；
根目录用 --projects-root 或环境变量 AGENT_SESSIONS_ROOT 指定。

字段位置（会话 jsonl 通用格式）：
    - 推理原文      type=reasoning 记录的 rawContent[].text
    - 真实思考 token providerData.usage.outputTokensDetails[].reasoning_tokens
    - 模型名        providerData.model（注意：子代理模型可能与主会话模型不同！）
    - 工具污染计数  type=function_call 记录数（必须 =0 才说明样本干净、可比）

使用方法：
    1) 在主会话派 N 个子代理跑题集（基线/加约束各若干次重复），
       提示词必须显式禁用一切工具，否则样本被 agent 层污染不可用；
    2) 记下每个子代理的 agent-id 与条件标签，写进 runs.json（格式见 runs.example.json）；
    3) python collect_builtin.py --config runs.json
    4) 核对输出里每行 fn_call=0，任何 fn_call>0 的样本剔除重跑。

标准答案判定：按题号【答案1】…【答案N】提取数字，与 runs.json 里的 gold 逐题比对，
容差 max(绝对 0.01, 相对 0.01%)（兼容合法四舍五入，如 1632.48 答成 1632.5）。
"""
import os, json, glob, re, argparse, time

# 会话落盘根目录（用 --projects-root 或 env AGENT_SESSIONS_ROOT 覆盖）
PROJECTS = os.environ.get('AGENT_SESSIONS_ROOT',
                          os.path.join(os.path.expanduser('~'), 'agent-sessions'))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results')
os.makedirs(OUT, exist_ok=True)


def find_subagent_files(projects_root):
    """扫描全部会话下的子代理 jsonl，返回 {agent_id: 文件路径}。"""
    found = {}
    pattern = os.path.join(projects_root, '*', '*', 'subagents', '*.jsonl')
    pattern2 = os.path.join(projects_root, '*', 'subagents', '*.jsonl')
    for fp in glob.glob(pattern) + glob.glob(pattern2):
        found[os.path.splitext(os.path.basename(fp))[0]] = fp
    return found


def parse(fp):
    recs = [json.loads(l) for l in open(fp, encoding='utf-8') if l.strip()]
    rchars = rtoks = in_toks = out_toks = nfc = 0
    model = None
    answer_text = ''
    t_start = t_end = None
    for x in recs:
        p = x.get('providerData') or {}
        model = model or p.get('model')
        ts = x.get('timestamp')
        if isinstance(ts, (int, float)):
            t_start = ts if t_start is None else min(t_start, ts)
            t_end = ts if t_end is None else max(t_end, ts)
        if x.get('type') == 'reasoning':
            rchars += sum(len(b.get('text') or '') for b in (x.get('rawContent') or []))
        if x.get('type') == 'function_call':
            nfc += 1
        u = p.get('usage')
        if isinstance(u, dict):
            in_toks += u.get('inputTokens') or 0
            out_toks += u.get('outputTokens') or 0
            for d in (u.get('outputTokensDetails') or []):
                if isinstance(d, dict) and 'reasoning_tokens' in d:
                    rtoks += d['reasoning_tokens']
        if x.get('type') == 'message':
            c = x.get('content')
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get('type') == 'output_text':
                        answer_text += b.get('text', '')
    return dict(model=model, rchars=rchars, rtoks=rtoks, in_toks=in_toks,
                out_toks=out_toks, nfc=nfc, answer_text=answer_text,
                wall=((t_end - t_start) / 1000.0) if (t_start and t_end) else None)


def judge(answer_text, gold):
    """gold: ['1632.48','15',...] 按【答案N】行比对。"""
    got, oks = [], []
    for i in range(1, len(gold) + 1):
        m = re.search(r'【答案%d】\s*([-0-9][0-9,\.]*)' % i, answer_text)
        got.append(m.group(1).replace(',', '') if m else None)
    for g, s in zip(gold, got):
        try:
            g, s = float(g), float(s)
            oks.append(abs(g - s) < max(0.01, abs(g) * 0.0001))
        except Exception:
            oks.append(False)
    return got, oks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     'runs.example.json'))
    ap.add_argument('--projects-root', default=PROJECTS)
    a = ap.parse_args()

    cfg = json.load(open(a.config, encoding='utf-8'))
    gold = cfg['gold']                      # 每题标准答案，顺序对应【答案N】
    runs = cfg['runs']                      # [{agent, model_tag, suffix, rep}]
    files = find_subagent_files(a.projects_root)

    rows = []
    for r in runs:
        aid = r['agent']
        fp = files.get(aid)
        if not fp:
            print('!! 未找到子代理文件: %s（检查 --projects-root 与 agent-id）' % aid)
            continue
        d = parse(fp)
        got, oks = judge(d['answer_text'], gold)
        rows.append(dict(agent=aid, model=r.get('model_tag') or d['model'],
                         suffix=r['suffix'], rep=r['rep'],
                         correct=sum(oks), n=len(gold), oks=oks, got=got,
                         rchars=d['rchars'], rtoks=d['rtoks'],
                         in_toks=d['in_toks'], out_toks=d['out_toks'],
                         nfc=d['nfc'], wall=d['wall']))
        flag = '' if d['nfc'] == 0 else '  [!] fn_call=%d 样本被工具调用污染，剔除重跑' % d['nfc']
        print('%-16s %-10s %-8s rep%-2d: %d/%d  rchars=%-6d rtoks=%-6d%s'
              % (aid, rows[-1]['model'], r['suffix'], r['rep'], sum(oks), len(gold),
                 d['rchars'], d['rtoks'], flag))

    stats = []
    for model_tag in sorted({r['model'] for r in rows}):
        for suffix in sorted({r['suffix'] for r in rows}):
            rs = [r for r in rows if r['model'] == model_tag and r['suffix'] == suffix]
            if not rs:
                continue
            n_call = len(rs) * len(gold)
            ok_call = sum(r['correct'] for r in rs)
            st = dict(model=model_tag, suffix=suffix, effort='high', set='long',
                      reps=len(rs), n=n_call, correct=ok_call,
                      accuracy=round(ok_call / n_call, 3),
                      rchars_mean=round(sum(r['rchars'] for r in rs) / len(rs), 1),
                      rtoks_mean=round(sum(r['rtoks'] for r in rs) / len(rs), 1),
                      fn_call_total=sum(r['nfc'] for r in rs),
                      rows=rs)
            stats.append(st)
            print('STAT %s %s: acc=%d/%d rchars_mean=%.0f rtoks_mean=%.0f fn_call=%d'
                  % (model_tag, suffix, ok_call, n_call, st['rchars_mean'],
                     st['rtoks_mean'], st['fn_call_total']))

    out = os.path.join(OUT, 'bench_builtin_%s.json' % time.strftime('%Y%m%d_%H%M%S'))
    json.dump(stats, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved ->', out)


if __name__ == '__main__':
    main()

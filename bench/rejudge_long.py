#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按修正后的容差重判长链评测结果（不重新调用 API，只重算已有行）
============================================================
背景：初版判分用绝对容差 0.01，把「1632.48 答成 1632.5」这类合法四舍五入误判为错。
修正为 max(绝对 0.01, 相对 0.01%) 后重判，并打印每一处判定翻转，供审计。

同时统计 rlen=0（厂商未回传思考流）的样本占比 —— 该指标决定长度数据是否可用。

用法: python rejudge_long.py [bench_long_*.json]
"""
import os, sys, json, glob

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'results')


def judge(gold, got):
    if got is None:
        return False
    try:
        g, x = float(gold), float(got)
    except Exception:
        return False
    return abs(g - x) < max(0.01, abs(g) * 0.0001)


def stat(rows):
    ok = sum(1 for r in rows if r['ok'])
    rl = sorted(r['rlen'] for r in rows if r['rlen'] > 0)   # 排除未回传思考流的样本
    rl_all = sorted(r['rlen'] for r in rows)
    wl = sorted(r['wall'] for r in rows)
    nz = sum(1 for r in rows if r['rlen'] == 0)
    return dict(n=len(rows), correct=ok, accuracy=round(ok / len(rows), 3),
                rlen_median=(rl[len(rl) // 2] if rl else 0),
                rlen_mean=(round(sum(rl) / len(rl), 1) if rl else 0),
                rlen_median_raw=(rl_all[len(rl_all) // 2] if rl_all else 0),
                wall_median=(round(wl[len(wl) // 2], 1) if wl else 0),
                wall_mean=(round(sum(wl) / len(wl), 1) if wl else 0),
                rlen_zero_n=nz, rlen_zero_pct=round(100 * nz / len(rows), 1))


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    fp = arg if arg else sorted(glob.glob(os.path.join(DATA, 'bench_long_*.json')))[-1]
    d = json.load(open(fp, encoding='utf-8'))
    print('重判:', fp)
    print('容差: max(绝对 0.01, 相对 0.01%)\n')

    flips = 0
    for x in d:
        for r in x['rows']:
            old = r['ok']
            new = judge(r['gold'], r['got'])
            if old != new:
                flips += 1
                print('  翻转 %s/%s %s rep%s: got=%s gold=%s  %s -> %s'
                      % (x['model'], x['suffix'], r['pid'], r.get('rep'),
                         r['got'], r['gold'], '错' if not old else '对',
                         '对' if new else '错'))
            r['ok'] = new
        st = stat(x['rows'])
        x.update({k: st[k] for k in ('correct', 'accuracy', 'rlen_median', 'rlen_mean',
                                     'rlen_median_raw', 'wall_median', 'wall_mean',
                                     'rlen_zero_n', 'rlen_zero_pct')})
    print('\n翻转总数:', flips)

    print('\n%-18s %-9s %-8s %6s %9s %9s %9s %8s %9s' %
          ('model', 'suffix', 'pid', '正确', 'rlen中位', 'rlen均值', 'rlen中位(含0)', '墙钟中位', 'rlen=0占比'))
    for x in sorted(d, key=lambda z: (z['model'], z['suffix'], str(z.get('pid')))):
        print('%-18s %-9s %-8s %3d/%-3d %9d %9.1f %9d %8.1f %8.1f%%' %
              (x['model'], x['suffix'], x.get('pid'), x['correct'], x['n'],
               x['rlen_median'], x['rlen_mean'], x['rlen_median_raw'],
               x['wall_median'], x['rlen_zero_pct']))

    out = fp.replace('.json', '_rejudged.json')
    json.dump(d, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\nsaved ->', out)


if __name__ == '__main__':
    main()

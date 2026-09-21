#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按题配对聚合（paired-by-problem）—— 长链/标准题集的正式口径
============================================================
为什么不能「把所有题的样本混在一起取中位数」：
  题集里各题难度不同，基线长度天然差一个量级（L1 6152 字符 vs L2 2020 字符）。
  混池取中位时，两组的「中位数落在哪道题上」可能不同 → 混进题目难度这个混淆变量。
  实测差异：混池中位得 -16.5%，按题配对后得 -33.3%，差了一倍。

正确做法（本脚本）：
  1) 每题分别算 基线中位 / 约束中位；
  2) 每题算压缩率 ratio = 约束中位 / 基线中位 − 1；
  3) 跨题取 ratio 的中位与均值 → 这才是「中间层的效果」；
  4) 正确率同理按题配对，并汇总总答对数。

用法: python aggregate_paired.py
"""
import os, json, glob
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'results')


def med(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def mean(xs):
    return sum(xs) / len(xs) if xs else 0


def paired(rows_by_pid, label, unit):
    """rows_by_pid: {suffix: {pid: [rows]}}"""
    pids = sorted(set(rows_by_pid['C0_base']) & set(rows_by_pid['C1_weak']))
    ratios, walls, detail = [], [], []
    ok_b = ok_c = n_b = n_c = 0
    for p in pids:
        b = rows_by_pid['C0_base'][p]
        c = rows_by_pid['C1_weak'][p]
        rb = med([r['rlen'] for r in b if r['rlen'] > 0])
        rc = med([r['rlen'] for r in c if r['rlen'] > 0])
        wb = med([r['wall'] for r in b])
        wc = med([r['wall'] for r in c])
        if rb > 0:
            ratios.append(rc / rb - 1)
            walls.append(wc / wb - 1 if wb else 0)
        ok_b += sum(1 for r in b if r['ok']); n_b += len(b)
        ok_c += sum(1 for r in c if r['ok']); n_c += len(c)
        detail.append((p, rb, rc, (rc / rb - 1) if rb else 0,
                       sum(1 for r in b if r['ok']), len(b),
                       sum(1 for r in c if r['ok']), len(c), wb, wc))
    # 混池口径（按模型跨题混合，不配对）：README §4.3 自陈 -16.5% 即此口径
    all_base = [r['rlen'] for p in pids for r in rows_by_pid['C0_base'][p] if r['rlen'] > 0]
    all_weak = [r['rlen'] for p in pids for r in rows_by_pid['C1_weak'][p] if r['rlen'] > 0]
    pooled = round((med(all_weak) / med(all_base) - 1) * 100, 1) if all_base and med(all_base) > 0 else 0.0
    return dict(label=label, unit=unit, n_pairs=len(pids),
                ratio_median=round(med(ratios) * 100, 1),
                ratio_mean=round(mean(ratios) * 100, 1),
                wall_median=round(med(walls) * 100, 1),
                pooled_median=pooled,
                ok_base=ok_b, n_base=n_b, ok_mid=ok_c, n_mid=n_c,
                detail=detail)


def aggregate_long_file(fp, report):
    """单个 bench_long_*_rejudged.json → 按模型产出长链题集的配对 + 混池口径。"""
    d = json.load(open(fp, encoding='utf-8'))
    stamp = os.path.basename(fp).replace('bench_long_', '').replace('_rejudged.json', '')
    by = defaultdict(lambda: defaultdict(list))
    zero = defaultdict(int)
    tot = defaultdict(int)
    for x in d:
        if x.get('pid') == 'ALL':
            continue
        for r in x['rows']:
            by[(x['model'], x['suffix'])][x['pid']].append(r)
            tot[(x['model'], x['suffix'])] += 1
            if r['rlen'] == 0:
                zero[(x['model'], x['suffix'])] += 1
    for m in sorted({k[0] for k in by}):
        zp = max(zero[(m, 'C0_base')] / max(tot[(m, 'C0_base')], 1),
                 zero[(m, 'C1_weak')] / max(tot[(m, 'C1_weak')], 1))
        rows_by_pid = {'C0_base': {}, 'C1_weak': {}}
        for s in ('C0_base', 'C1_weak'):
            for p, rs in by[(m, s)].items():
                rows_by_pid[s][p] = rs
        r = paired(rows_by_pid, '%s · 长链高耗题（数据集 %s）' % (m, stamp), '字符')
        r['rlen_zero_pct'] = round(zp * 100, 1)
        r['usable'] = zp <= 0.10
        report.append(r)


def main():
    report = []

    # ---------- A. 长链题集（外部模型，字符口径）----------
    # 遍历 results/ 下全部 bench_long_*_rejudged.json（默认覆盖 095010/100715 等全部数据集）
    fps = sorted(p for p in glob.glob(os.path.join(DATA, 'bench_long_*.json')) if '_rejudged' in p)
    for fp in fps:
        aggregate_long_file(fp, report)

    # ---------- B. 标准推理题集（外部模型，字符口径）----------
    fps = sorted(glob.glob(os.path.join(DATA, 'bench_high_*.json')))
    if fps:
        d = json.load(open(fps[-1], encoding='utf-8'))
        by = defaultdict(lambda: defaultdict(list))
        for x in d:
            for r in x['rows']:
                by[(x['model'], x['suffix'])][r['pid']].append(r)
        for m in sorted({k[0] for k in by}):
            if (m, 'C1_weak') not in by:
                continue
            rows_by_pid = {'C0_base': dict(by[(m, 'C0_base')]), 'C1_weak': dict(by[(m, 'C1_weak')])}
            r = paired(rows_by_pid, '%s · 标准推理题 13 题' % m, '字符')
            r['rlen_zero_pct'] = 0.0
            r['usable'] = True
            report.append(r)

    # ---------- C. 内置模型（子代理，token 口径）----------
    fps = sorted(glob.glob(os.path.join(DATA, 'bench_builtin_*.json')))
    if fps:
        d = json.load(open(fps[-1], encoding='utf-8'))
        by = {x['suffix']: x for x in d}
        if 'C0_base' in by and 'C1_weak' in by:
            b, c = by['C0_base'], by['C1_weak']
            rb = mean([r['rtoks'] for r in b['rows']])
            rc = mean([r['rtoks'] for r in c['rows']])
            report.append(dict(label='%s（内置） · 长链高耗题 5 题×3 次' % b['model'],
                               unit='token', n_pairs=1,
                               ratio_median=round((rc / rb - 1) * 100, 1),
                               ratio_mean=round((rc / rb - 1) * 100, 1),
                               wall_median=0.0,
                               ok_base=b['correct'], n_base=b['n'],
                               ok_mid=c['correct'], n_mid=c['n'],
                               rlen_zero_pct=0.0, usable=True,
                               abs_base=round(rb, 1), abs_mid=round(rc, 1)))

    # ---------- 输出 ----------
    print('=' * 96)
    print('按题配对聚合结果（口径：每题先取中位，再跨题取压缩率中位）')
    print('=' * 96)
    for r in report:
        flag = '' if r['usable'] else '  ⚠ 长度数据不可用（rlen=0 占比 %.0f%%）' % r['rlen_zero_pct']
        print('\n%s%s' % (r['label'], flag))
        if r['usable']:
            print('  思考量压缩：中位 %+.1f%%   均值 %+.1f%%   （%s 口径，%d 组配对）'
                  % (r['ratio_median'], r['ratio_mean'], r['unit'], r['n_pairs']))
            if 'pooled_median' in r:
                print('  混池口径（不配对）：%+.1f%%' % r['pooled_median'])
            if r.get('abs_base'):
                print('  绝对值：%g → %g %s' % (r['abs_base'], r['abs_mid'], r['unit']))
        print('  正确率：%d/%d → %d/%d' % (r['ok_base'], r['n_base'], r['ok_mid'], r['n_mid']))
        if r.get('wall_median') and r['wall_median'] != 0:
            print('  墙钟变化：%+.1f%%（并发压测口径，仅供参考）' % r['wall_median'])
        if r.get('detail'):
            print('  %-5s %10s %10s %9s %9s %10s' % ('题', '基线中位', '约束中位', '压缩', '正确(基)', '正确(约)'))
            for (p, rb, rc, ra, ob, nb, oc, nc, wb, wc) in r['detail']:
                print('  %-5s %10d %10d %8.0f%% %6d/%-3d %6d/%-3d'
                      % (p, rb, rc, ra * 100, ob, nb, oc, nc))

    out = os.path.join(DATA, 'aggregate_paired.json')
    json.dump(report, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\nsaved ->', out)


if __name__ == '__main__':
    main()

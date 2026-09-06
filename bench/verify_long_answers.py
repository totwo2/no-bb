#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长链题集标准答案 · 独立复算脚本（可复算要求：任何进事实底账的数必须能重跑出来）
============================================================
本脚本与 bench_long.py 的题目文本**解耦**——不读题、不调模型，
只用题面给出的数字假设做纯数学复算，输出每题标准答案。
若本脚本输出与 bench_long.py 里 LONG_PROBLEMS 的 answer 字段不一致，说明题面或答案有误。

用法: python verify_long_answers.py
"""
import sys


def l1_finance():
    """L1 五年财务测算：营收 +25%/年，成本=营收62%，销售费用 +10%/年，管理80，折旧50，税率25%"""
    rev, sales, total, rows = 1200.0, 150.0, 0.0, []
    for y in range(1, 6):
        if y > 1:
            rev *= 1.25
            sales *= 1.10
        cost = rev * 0.62
        ebit = rev - cost - sales - 80 - 50
        ni = ebit * 0.75  # 税率 25%
        rows.append((y, rev, cost, sales, ebit, ni))
        total += ni
    for y, rev, cost, s, ebit, ni in rows:
        print('  Y%d 营收=%.2f 成本=%.2f 销售费用=%.2f 营业利润=%.4f 净利润=%.4f'
              % (y, rev, cost, s, ebit, ni))
    return round(total, 2)


def l2_critical_path():
    """L2 关键路径：7 任务依赖网络的最早完成时间（makespan）"""
    dur = {'A': 3, 'B': 4, 'C': 2, 'D': 5, 'E': 3, 'F': 2, 'G': 1}
    pre = {'A': [], 'B': ['A'], 'C': ['A'], 'D': ['B'], 'E': ['C'], 'F': ['D', 'E'], 'G': ['F']}
    memo = {}

    def ef(t):
        if t in memo:
            return memo[t]
        memo[t] = dur[t] + max([ef(p) for p in pre[t]] or [0])
        return memo[t]

    ms = max(ef(t) for t in dur)
    print('  各任务最早完成时刻:', memo)
    return ms


def l3_inclusion_exclusion():
    """L3 容斥：1..500 中能被3或5整除、但不能被7整除的个数"""
    N = 500
    n3, n5, n15 = N // 3, N // 5, N // 15
    n7, n21, n35, n105 = N // 7, N // 21, N // 35, N // 105
    a = n3 + n5 - n15          # 能被 3 或 5 整除
    b = n21 + n35 - n105       # 能被 7 整除 且 能被 3 或 5 整除
    print('  [3]=%d [5]=%d [15]=%d → [3或5]=%d ; [21]=%d [35]=%d [105]=%d → [且7]=%d'
          % (n3, n5, n15, a, n21, n35, n105, b))
    return a - b


def l4_discount_chain():
    """L4 折扣链：85折 → 每满2000减300 → 满3000减400 → 运费60 → 服务费2%"""
    p1 = 5000 * 0.85
    p2 = p1 - int(p1 // 2000) * 300
    p3 = p2 - 400 if p2 >= 3000 else p2
    ship = 60.0
    fee = p3 * 0.02
    print('  85折=%.2f 满减后=%.2f 券后=%.2f 运费=%.2f 服务费=%.2f'
          % (p1, p2, p3, ship, fee))
    return round(p3 + ship + fee, 2)


def l5_flowshop():
    """L5 流水车间：3 机器 × 6 订单，固定 J1..J6 投料顺序，求 makespan"""
    J = {'J1': (2, 3, 1), 'J2': (1, 2, 4), 'J3': (3, 1, 2),
         'J4': (2, 4, 3), 'J5': (4, 2, 1), 'J6': (1, 3, 2)}
    free = [0.0, 0.0, 0.0]
    for name in ['J1', 'J2', 'J3', 'J4', 'J5', 'J6']:
        prev_done = 0.0
        for k in range(3):
            st = free[k] if k == 0 else max(free[k], free[k - 1])
            free[k] = st + J[name][k]
        print('  %s 完工时刻 M1=%.0f M2=%.0f M3=%.0f' % (name, free[0], free[1], free[2]))
    return int(max(free))


GOLD = {'L1': 1632.48, 'L2': 15, 'L3': 200, 'L4': 3375, 'L5': 19}


def main():
    print('长链题集标准答案 · 独立复算')
    got = {}
    for pid, fn, desc in (('L1', l1_finance, '五年财务测算'),
                          ('L2', l2_critical_path, '关键路径排程'),
                          ('L3', l3_inclusion_exclusion, '容斥计数'),
                          ('L4', l4_discount_chain, '折扣税费链'),
                          ('L5', l5_flowshop, '流水车间排程')):
        print('%s %s:' % (pid, desc))
        got[pid] = fn()
        print('  → %s 标准答案 = %s' % (pid, got[pid]))

    print('\n校验（脚本复算 vs bench_long.py 题面声明的答案）:')
    bad = 0
    for pid in GOLD:
        ok = abs(float(got[pid]) - float(GOLD[pid])) < 0.01
        bad += 0 if ok else 1
        print('  %s: 复算=%s 声明=%s %s' % (pid, got[pid], GOLD[pid], 'OK' if ok else '*** 不一致 ***'))
    print('\n结论:', '全部一致，答案可用' if bad == 0 else '存在 %d 处不一致，禁止入库' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

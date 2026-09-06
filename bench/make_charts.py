#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 README 用图表（纯 SVG，无 JS 依赖，GitHub 可直接显示）
============================================================
口径纪律：
  不同模型的思考量**单位不同**（step/agnes=reasoning 字符，内置通道 hy3=厂商上报
  reasoning_tokens），绝对量不可混用。故：
    图1 用「相对基线归一化 %」——跨模型可比，因为都是各自基线的相对变化率；
    图2 用「压缩率 vs 正确率」散点——两轴同为无量纲比率，跨模型可比。
  绝对值一律只在表格里按模型分列标注单位，绝不画进同一根轴。

输出：assets/bench_saving.svg（图1 归一化柱状）
      assets/bench_scatter.svg（图2 压缩率-正确率散点）
用法: python make_charts.py
"""
import os, json, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, 'assets')
os.makedirs(ASSETS, exist_ok=True)

# 主题（浅色）
C_TEXT = '#1a1a1a'
C_GRID = '#e3e3e3'
C_BASE = '#9aa5b1'   # 基线：灰
C_MID = '#2e9e5b'    # 中间层：绿
C_ACC = '#e8722c'    # 正确率：橙


def load_all():
    """图表数据统一取自 aggregate_paired.json（按题配对口径）+ 内置模型采集结果。
    口径纪律：绝对量不跨模型混轴（字符 vs token），图中只画「相对各自基线的 %」。"""
    out = []
    fp = os.path.join(HERE, '..', 'results', 'aggregate_paired.json')
    if os.path.exists(fp):
        for r in json.load(open(fp, encoding='utf-8')):
            if not r.get('usable'):
                continue
            base_abs = r.get('abs_base')
            mid_abs = r.get('abs_mid')
            out.append(dict(model=r['label'].split(' · ')[0],
                            scene=r['label'].split(' · ')[1] if ' · ' in r['label'] else '',
                            unit=r['unit'],
                            base_think=base_abs if base_abs else r['ratio_mean'],
                            mid_think=mid_abs if mid_abs else (100 + r['ratio_mean']),
                            ratio=r['ratio_median'],
                            base_ok=r['ok_base'], n_base=r['n_base'],
                            mid_ok=r['ok_mid'], n_mid=r['n_mid']))
    return out


def esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def chart_saving(rows, path):
    """图1：归一化分组柱状图（各自基线=100%）"""
    W, H = 720, 380
    pad_l, pad_r, pad_t, pad_b = 58, 18, 46, 92
    pw, ph = W - pad_l - pad_r, H - pad_t - pad_b
    ymax = 110
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
             'font-family="Segoe UI,system-ui,sans-serif">' % (W, H, W, H)]
    parts.append('<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H))
    parts.append('<text x="%d" y="26" font-size="15" font-weight="600" fill="%s">'
                 '接入中间层后的思考量（各自无约束基线 = 100%%）</text>' % (pad_l - 40, C_TEXT))
    # y 网格
    for v in range(0, ymax + 1, 20):
        y = pad_t + ph - (v / ymax) * ph
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>'
                     % (pad_l, y, pad_l + pw, y, C_GRID))
        parts.append('<text x="%d" y="%.1f" font-size="10" fill="#666" text-anchor="end">%d%%</text>'
                     % (pad_l - 6, y + 3.5, v))
    # 100% 参考线加粗
    y100 = pad_t + ph - (100 / ymax) * ph
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#888" stroke-width="1.5" '
                 'stroke-dasharray="4 3"/>' % (pad_l, y100, pad_l + pw, y100))

    n = len(rows)
    slot = pw / n
    bw = min(52, slot * 0.30)
    for i, r in enumerate(rows):
        cx = pad_l + slot * (i + 0.5)
        pct = 100 + r['ratio']          # 配对口径：中间层相对基线的 %
        # 基线柱
        x0 = cx - bw - 4
        h0 = (100 / ymax) * ph
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" rx="2"/>'
                     % (x0, pad_t + ph - h0, bw, h0, C_BASE))
        parts.append('<text x="%.1f" y="%.1f" font-size="10" fill="#555" text-anchor="middle">100%%</text>'
                     % (x0 + bw / 2, pad_t + ph - h0 - 5))
        # 中间层柱
        x1 = cx + 4
        h1 = (pct / ymax) * ph
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" rx="2"/>'
                     % (x1, pad_t + ph - h1, bw, h1, C_MID))
        delta = r['ratio']
        col = C_MID if delta < 0 else '#c62828'
        parts.append('<text x="%.1f" y="%.1f" font-size="11" font-weight="700" fill="%s" '
                     'text-anchor="middle">%+.0f%%</text>' % (x1 + bw / 2, pad_t + ph - h1 - 5, col, delta))
        # x 标签
        parts.append('<text x="%.1f" y="%.1f" font-size="11" fill="%s" text-anchor="middle">%s</text>'
                     % (cx, pad_t + ph + 18, C_TEXT, esc(r['model'])))
        parts.append('<text x="%.1f" y="%.1f" font-size="9.5" fill="#777" text-anchor="middle">%s</text>'
                     % (cx, pad_t + ph + 32, esc(r['scene'])))
        parts.append('<text x="%.1f" y="%.1f" font-size="9.5" fill="#999" text-anchor="middle">'
                     '思考量按题配对中位 · %s</text>'
                     % (cx, pad_t + ph + 46, esc(r['unit'])))
    # 图例
    lx, ly = pad_l, H - 20
    parts.append('<rect x="%d" y="%d" width="11" height="11" fill="%s" rx="2"/>' % (lx, ly - 9, C_BASE))
    parts.append('<text x="%d" y="%d" font-size="10.5" fill="#555">无约束基线</text>' % (lx + 16, ly))
    parts.append('<rect x="%d" y="%d" width="11" height="11" fill="%s" rx="2"/>' % (lx + 92, ly - 9, C_MID))
    parts.append('<text x="%d" y="%d" font-size="10.5" fill="#555">接入收敛约束中间层</text>' % (lx + 108, ly))
    parts.append('</svg>')
    open(path, 'w', encoding='utf-8').write('\n'.join(parts))
    print(' ->', path)


def chart_scatter(rows, path):
    """图2：压缩率(横) vs 正确率(纵) 散点——右下角=省得多且不掉分=理想区"""
    W, H = 720, 400
    pad_l, pad_r, pad_t, pad_b = 62, 22, 46, 78
    pw, ph = W - pad_l - pad_r, H - pad_t - pad_b
    xs = [-r['ratio'] for r in rows]   # 压缩率（正 = 省）
    xmin, xmax = -25, 70
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
             'font-family="Segoe UI,system-ui,sans-serif">' % (W, H, W, H)]
    parts.append('<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H))
    parts.append('<text x="%d" y="26" font-size="15" font-weight="600" fill="%s">'
                 '省得越多、正确率越不掉 = 越靠右下角（理想区）</text>' % (pad_l - 40, C_TEXT))
    # 理想区底色
    xg0 = pad_l + (35 - xmin) / (xmax - xmin) * pw
    yg0 = pad_t + ph - (0.95 / 1.05) * ph * 1.0
    parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#e8f5e9" opacity="0.75"/>'
                 % (xg0, pad_t, pad_l + pw - xg0, ph * (0.95 - 0.05)))
    parts.append('<text x="%.1f" y="%.1f" font-size="10.5" fill="#4caf50" font-weight="600">理想区</text>'
                 % (xg0 + 8, pad_t + 16))
    # 网格
    for v in range(-20, 71, 10):
        x = pad_l + (v - xmin) / (xmax - xmin) * pw
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s"/>'
                     % (x, pad_t, x, pad_t + ph, C_GRID))
        parts.append('<text x="%.1f" y="%d" font-size="10" fill="#666" text-anchor="middle">%d%%</text>'
                     % (x, pad_t + ph + 16, v))
    for v in (0.6, 0.7, 0.8, 0.9, 1.0):
        y = pad_t + ph - (v - 0.5) / 0.55 * ph
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
                     % (pad_l, y, pad_l + pw, y, C_GRID))
        parts.append('<text x="%d" y="%.1f" font-size="10" fill="#666" text-anchor="end">%.0f%%</text>'
                     % (pad_l - 6, y + 3.5, v * 100))
    # 零线
    x0 = pad_l + (0 - xmin) / (xmax - xmin) * pw
    parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#888" stroke-dasharray="4 3" '
                 'stroke-width="1.2"/>' % (x0, pad_t, x0, pad_t + ph))
    parts.append('<text x="%.1f" y="%d" font-size="9.5" fill="#888" text-anchor="middle">无效果</text>'
                 % (x0, pad_t - 6))

    # 分色（重叠点可分辨）+ 标签上下错位（重合点标签不打架）
    PALETTE = ['#2e9e5b', '#1f77b4', '#e8722c', '#9467bd']
    label_side = {}
    seen_key = {}
    for i, (r, xv) in enumerate(zip(rows, xs)):
        acc = r['mid_ok'] / r['n_mid']
        x = pad_l + (xv - xmin) / (xmax - xmin) * pw
        y = pad_t + ph - (acc - 0.5) / 0.55 * ph
        # 同一落点附近（<26px）已有点 → 标签换边
        key = (round(x / 26), round(y / 26))
        side = 'up'
        if key in seen_key:
            side = 'down'
            seen_key[key] += 1
        else:
            seen_key[key] = 1
        label_side[i] = (x, y, side)
        col = PALETTE[i % len(PALETTE)]
        parts.append('<circle cx="%.1f" cy="%.1f" r="8" fill="%s" opacity="0.88" '
                     'stroke="#fff" stroke-width="1.5"/>' % (x, y, col))
        name = r['model']
        ty = y - 16 if side == 'up' else y + 26
        parts.append('<text x="%.1f" y="%.1f" font-size="10.5" fill="%s" text-anchor="middle" '
                     'font-weight="600">%s</text>' % (x, ty, col, esc(name)))
        parts.append('<text x="%.1f" y="%.1f" font-size="9" fill="#666" text-anchor="middle">'
                     '%+.0f%% · %d/%d</text>' % (x, ty + (13 if side == 'up' else -20),
                                                 xv, r['mid_ok'], r['n_mid']))
    parts.append('<text x="%d" y="%d" font-size="10.5" fill="#555" text-anchor="middle">'
                 '思考量压缩率（越大越省）→</text>' % (pad_l + pw / 2, H - 34))
    parts.append('<text x="18" y="%.1f" font-size="10.5" fill="#555" '
                 'transform="rotate(-90 18 %.1f)" text-anchor="middle">接入后正确率 →</text>'
                 % (pad_t + ph / 2, pad_t + ph / 2))
    parts.append('</svg>')
    open(path, 'w', encoding='utf-8').write('\n'.join(parts))
    print(' ->', path)


def main():
    rows = load_all()
    if not rows:
        print('!! 无数据')
        return
    for r in rows:
        print('  %-22s %-22s 压缩 %+.1f%%（%s）  正确 %d/%d → %d/%d'
              % (r['model'], r['scene'], r['ratio'], r['unit'],
                 r['base_ok'], r['n_base'], r['mid_ok'], r['n_mid']))
    chart_saving(rows, os.path.join(ASSETS, 'bench_saving.svg'))
    chart_scatter(rows, os.path.join(ASSETS, 'bench_scatter.svg'))


if __name__ == '__main__':
    main()

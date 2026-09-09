#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nobb_converge_hook.py —— No BB 收敛约束注入（UserPromptSubmit）
============================================================
原理：WorkBuddy 的 UserPromptSubmit hook 协议支持 additionalContext——
注入文本会进入本次 LLM 调用的入参。本 hook 把「想明白就收口」约束
经此官方挂点注入，所有模型（含平台内置免费模型）每条消息自动带上。
中间层（管道）服务自费 key 场景；本 hook 服务平台内置场景。零 key。

配置：
  ~/.workbuddy/hooks/nobb-converge.txt      约束词（可随时改，即时生效）
  ~/.workbuddy/hooks/nobb-converge.off      存在此文件 = 暂停注入（开关）

纪律：fail-open（任何异常都放行空注入，绝不阻塞对话）；日志只记触发事实。
"""
import json, os, sys, time

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
WORD_FILE = os.path.join(HOOK_DIR, 'nobb-converge.txt')
OFF_FILE = os.path.join(HOOK_DIR, 'nobb-converge.off')
LOG_FILE = os.path.join(HOOK_DIR, 'nobb-converge.log')

DEFAULT_WORD = ("（回复要求：想明白就回答——一旦得出结论，直接给出答案，"
                "不要反复推演、不要冗长复述思考过程。）")


def main():
    try:
        stdin = sys.stdin.read()
        payload = json.loads(stdin) if stdin.strip() else {}
    except Exception:
        payload = {}
    prompt = ''
    if isinstance(payload, dict):
        pr = payload.get('prompt')
        if isinstance(pr, str):
            prompt = pr

    word = ''
    if not os.path.exists(OFF_FILE):
        try:
            word = open(WORD_FILE, encoding='utf-8').read().strip() or DEFAULT_WORD
        except Exception:
            word = DEFAULT_WORD
        # 幂等：同一条消息若已带约束（重试/队列重放），不重复注入
        if prompt and word.strip('（）()') in prompt:
            word = ''

    out = {'continue': True,
           'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                  'additionalContext': word}}
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write('\n')

    # 日志：只记触发事实与注入与否（不含对话内容）
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('%s injected=%s prompt_len=%d\n'
                    % (time.strftime('%Y-%m-%dT%H:%M:%S'), 'yes' if word else 'no', len(prompt)))
    except Exception:
        pass


if __name__ == '__main__':
    main()

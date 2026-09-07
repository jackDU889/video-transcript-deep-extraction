#!/usr/bin/env python3
"""分析队列：独立于采集队列，轮询 输出/ 下「有 01 原始文案、无 02 导读」的目录，
逐个调用 analyze.py 生成分系列视角的深度学习导读初稿。

- 每个目录最多重试 2 次，仍失败则写 02_分析失败.txt
- 已有 02_深度学习导读_API初稿.md 的目录视为"已出初稿、待人工精修"，跳过
- 采集完成（采集完成.flag）且无待处理目录时退出
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
STATE = os.path.join(HERE, "队列状态_分析.json")


def pending():
    out = []
    for d in sorted(glob.glob(os.path.join(HERE, "输出", "*"))):
        if not os.path.isdir(d):
            continue
        m01 = os.path.join(d, "01_原始文案.md")
        m02 = os.path.join(d, "02_深度学习导读.md")
        draft = os.path.join(d, "02_深度学习导读_API初稿.md")
        meta = os.path.join(d, "meta.json")
        if (os.path.isfile(m01) and os.path.isfile(meta)
                and not os.path.isfile(m02) and not os.path.isfile(draft)):
            out.append(d)
    return out


def main():
    state = json.load(open(STATE)) if os.path.isfile(STATE) else {}
    flag = os.path.join(HERE, "采集完成.flag")
    idle = 0
    while True:
        pend = [d for d in pending() if state.get(d, 0) < 2]
        if not pend:
            if os.path.isfile(flag):
                idle += 1
                if idle >= 3:
                    break
            time.sleep(30)
            continue
        idle = 0
        d = pend[0]
        state[d] = state.get(d, 0) + 1
        print(f"[分析] {os.path.basename(d)}（第{state[d]}次尝试）", flush=True)
        try:
            r = subprocess.run([PY, os.path.join(HERE, "analyze.py"), d],
                               capture_output=True, text=True, timeout=2400, cwd=HERE)
            code, tail = r.returncode, (r.stdout + r.stderr)[-260:]
        except subprocess.TimeoutExpired:
            code, tail = -9, "超时"
        if code == 0:
            print("  ✅", flush=True)
        else:
            print(f"  ❌ {tail}", flush=True)
            if state[d] >= 2:
                open(os.path.join(d, "02_分析失败.txt"), "w").write(tail)
        json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
        time.sleep(5)
    print("[分析队列完成]", flush=True)


if __name__ == "__main__":
    main()

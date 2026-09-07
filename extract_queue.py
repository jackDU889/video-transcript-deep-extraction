#!/usr/bin/env python3
"""采集队列：按 视频清单.json 逐个提取文案（字幕优先/whisper兜底），与分析队列并行。

- 幂等：启动时扫描 输出/*/meta.json 中已完成的 bvid，自动跳过
- 顺序：按系列优先级 + 发布时间降序（新→旧）
- 防风控：每条之间随机停 2~5 秒
- 完成后写 采集完成.flag
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LIST_PATH = os.path.join(HERE, "视频清单.json")
PY = sys.executable
SERIES_ORDER = ["企业IT方法论与最佳实践", "韩工开物", "一家不平何以平天", "有意思的访谈", "运营商大起底", "其他"]


def done_bvids() -> set:
    done = set()
    for mp in glob.glob(os.path.join(HERE, "输出", "*", "meta.json")):
        try:
            b = json.load(open(mp)).get("bvid")
            if b:
                done.add(b)
        except Exception:
            pass
    return done


def main():
    manifest = json.load(open(LIST_PATH))
    vids = manifest["videos"]
    done = done_bvids()

    def key(v):
        s = v["series"] if v["series"] in SERIES_ORDER else "其他"
        return (SERIES_ORDER.index(s), -(v.get("pubdate") or 0))

    vids.sort(key=key)
    total = len(vids)
    ok_count = fail = skip = 0
    for i, v in enumerate(vids, 1):
        if v["bvid"] in done:
            skip += 1
            continue
        series = v["series"] if v["series"] in SERIES_ORDER else "其他"
        url = f"https://www.bilibili.com/video/{v['bvid']}"
        print(f"[采集 {i}/{total}] {series} | {v['title'][:32]} | {url}", flush=True)
        ok = False
        saw_412 = False
        for attempt in (1, 2):  # 412 风控退避重试
            try:
                r = subprocess.run([PY, os.path.join(HERE, "extract.py"), url, "--series", series],
                                   capture_output=True, text=True, timeout=7200, cwd=HERE)
            except subprocess.TimeoutExpired:
                print("  ❌ 超时", flush=True)
                break
            if r.returncode == 0:
                ok = True
                out = r.stdout or ""
                src = "字幕" if "bilibili_subtitle" in out else ("mlx" if "mlx-whisper" in out else "?")
                print(f"  ✅ ({src})", flush=True)
                break
            tail = (r.stdout + r.stderr)
            if "412" in tail:
                saw_412 = True
            if "412" in tail and attempt == 1:
                print("  ⚠ 412风控，退避45秒后重试…", flush=True)
                time.sleep(45)
                continue
            print(f"  ❌ {tail[-280:]}", flush=True)
            break
        ok_count += 1 if ok else 0
        fail += 0 if ok else 1
        if saw_412 and not ok:
            print("  ⚠ 连续412，冷却90秒…", flush=True)
            time.sleep(90)
        else:
            time.sleep(3 + (i % 4))
    print(f"[采集队列完成] 成功 {ok_count} | 失败 {fail} | 跳过 {skip}", flush=True)
    open(os.path.join(HERE, "采集完成.flag"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    main()

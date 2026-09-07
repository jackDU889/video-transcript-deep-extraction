#!/usr/bin/env python3
"""爬取B站UP主空间视频清单与系列归属 → 视频清单.json

用法: .venv/bin/python crawl_space.py [mid]   (默认 mid=519402468 老韩一米九)
"""
import json
import os
import sys
import time
from collections import Counter

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.subtitles import _mixin_key, _wbi_sign  # noqa: E402

MID = int(sys.argv[1]) if len(sys.argv) > 1 else 519402468
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "视频清单.json")
SESSDATA = open(os.path.expanduser("~/.bilibili_sessdata")).read().strip()
H = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0",
    "Referer": f"https://space.bilibili.com/{MID}/video",
    "Cookie": f"SESSDATA={SESSDATA}",
}


def api(url, params=None, wbi=False):
    if wbi:
        mixin = _mixin_key(SESSDATA)
        params = _wbi_sign({**(params or {}), "web_location": 333.1387}, mixin)
    r = requests.get(url, params=params, headers=H, timeout=20)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"{url} code={data.get('code')} {data.get('message')}")
    return data["data"]


def upsert(videos, a, series_name):
    bvid = a.get("bvid")
    if not bvid:
        return
    v = videos.get(bvid)
    if not v:
        videos[bvid] = {"bvid": bvid, "aid": a.get("aid"), "cid": a.get("cid"),
                        "title": (a.get("title") or "").replace("<em class=\"keyword\">", "").replace("</em>", ""),
                        "duration": a.get("duration", 0), "pubdate": a.get("pubdate") or a.get("created"),
                        "series": series_name, "series_all": [series_name]}
    elif series_name not in v["series_all"]:
        v["series_all"].append(series_name)


def main():
    videos = {}  # bvid -> dict

    # 1) 系列列表（meta.series_id/name/total）
    sl = api("https://api.bilibili.com/x/polymer/web-space/seasons_series_list",
             {"mid": MID, "page_num": 1, "page_size": 20}, wbi=True)
    items = sl.get("items_lists") or {}
    series_meta = [(s["meta"]["series_id"], s["meta"]["name"], int(s["meta"].get("total") or 0))
                   for s in (items.get("series_list") or [])]
    print("系列：", [(n, t) for _i, n, t in series_meta])

    # 2) 逐系列拉全量视频（series接口免wbi）
    series_map = {}
    for sid, name, total in series_meta:
        series_map[str(sid)] = name
        pn = 1
        while True:
            d = api("https://api.bilibili.com/x/series/archives",
                    {"mid": MID, "series_id": sid, "only_normal": False,
                     "sort": "desc", "pn": pn, "ps": 100})
            arcs = d.get("archives") or []
            for a in arcs:
                upsert(videos, a, name)
            if pn * 100 >= total or len(arcs) < 100:
                break
            pn += 1
            time.sleep(1.5)
        got = sum(1 for v in videos.values() if name in v["series_all"])
        print(f"  系列「{name}」应{total} 实得{got}")

    # 3) 全量视频列表兜底（含未入系列的视频）；此接口风控严格，失败不阻塞主流程
    pn = 1
    total_all = 0
    try:
        while True:
            d = api("https://api.bilibili.com/x/space/wbi/arc/search",
                    {"mid": MID, "pn": pn, "ps": 30, "order": "pubdate"}, wbi=True)
            page = d.get("page") or {}
            total_all = int(page.get("count") or 0)
            vlist = d.get("list", {}).get("vlist") or []
            for a in vlist:
                upsert(videos, a, "其他")
            if pn * 30 >= total_all or not vlist:
                break
            pn += 1
            time.sleep(1.5)
    except Exception as e:
        print(f"[警告] 全量兜底接口失败（{e}），仅产出系列视频；其余稍后经浏览器会话补爬")
    print(f"空间总视频数（接口口径）: {total_all}")

    out = {"mid": MID, "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "total": len(videos), "series_map": series_map, "videos": list(videos.values())}
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"\n✅ 共 {len(videos)} 个视频 → {OUT}")
    print("系列分布:", dict(Counter(v["series"] for v in videos.values())))


if __name__ == "__main__":
    main()

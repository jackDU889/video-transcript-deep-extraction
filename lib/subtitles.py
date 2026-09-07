"""B站字幕获取：wbi 签名调用 player API，拉取官方/AI字幕（需登录态 SESSDATA）。"""
import hashlib
import re
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
PLAYER_WBI_URL = "https://api.bilibili.com/x/player/wbi/v2"

# wbi 混淆表（B站公开约定，社区通用常量）
MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
             33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
             26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
             20, 34, 44, 52]

_cache = {}


def _mixin_key(sessdata: str) -> str:
    """从 nav 接口取 wbi_img 两个32位串，按混淆表重排得 32 位 mixin key。"""
    if "mixin" in _cache:
        return _cache["mixin"]
    r = requests.get(NAV_URL, headers={
        "User-Agent": UA, "Referer": "https://www.bilibili.com/",
        "Cookie": f"SESSDATA={sessdata}" if sessdata else "",
    }, timeout=20)
    data = r.json().get("data") or {}
    img = (data.get("wbi_img") or {}).get("img_url") or ""
    sub = (data.get("wbi_img") or {}).get("sub_url") or ""
    raw = (_filename(img) + _filename(sub))[:64]
    if len(raw) < 32:
        raise RuntimeError("无法获取 wbi key（nav 接口异常）")
    key = "".join(raw[i] for i in MIXIN_TAB)[:32]
    _cache["mixin"] = key
    return key


def _filename(url: str) -> str:
    base = url.rsplit("/", 1)[-1]
    return base.split(".")[0]


def _wbi_sign(params: dict, mixin: str) -> dict:
    params = dict(params)
    params["wts"] = int(time.time())
    params = {k: str(v) for k, v in sorted(params.items())}
    query = urllib.parse.urlencode(params, safe="!\"'()*-._~")
    params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return params


def get_bili_subtitles(bvid: str, aid: int, cid: int, sessdata: str):
    """返回 [(start, end, text)]；无字幕/无权限时返回 None。"""
    mixin = _mixin_key(sessdata)
    params = _wbi_sign({"aid": aid, "cid": cid, "bvid": bvid}, mixin)
    r = requests.get(PLAYER_WBI_URL, params=params, headers={
        "User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bvid}/",
        "Cookie": f"SESSDATA={sessdata}",
    }, timeout=20)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站player接口失败: code={data.get('code')} {data.get('message')}")
    subs = ((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return None
    # 优先中文字幕（ai-zh 为AI生成），其次任意一条
    subs.sort(key=lambda s: 0 if s.get("lan", "").startswith("zh") or s.get("lan") == "ai-zh" else 1)
    sub_url = subs[0].get("subtitle_url") or ""
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    if not sub_url:
        return None
    body = requests.get(sub_url, headers={"User-Agent": UA}, timeout=30).json()
    out = []
    for item in body.get("body") or []:
        text = (item.get("content") or "").strip()
        if text:
            out.append((float(item.get("from", 0)), float(item.get("to", 0)), text))
    return out or None

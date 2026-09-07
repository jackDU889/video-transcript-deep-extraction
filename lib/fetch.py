"""视频/音频获取：yt-dlp 封装（元数据探测、短链解析、音频下载）。"""
import os
import re
import uuid

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _ytdlp_opts(page_url: str, cookies_browser: str = "") -> dict:
    opts = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "http_headers": {"User-Agent": UA, "Referer": page_url},
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser.strip().lower(), None, None, None)
    return opts


def resolve_short_url(url: str) -> str:
    """解析 b23.tv / v.douyin.com 等短链，返回真实页面URL。"""
    if "b23.tv" not in url and "v.douyin.com" not in url:
        return url
    try:
        r = requests.get(url, headers={"User-Agent": UA}, allow_redirects=True, timeout=20)
        final = r.url
        if "b23.tv" in url and "bilibili.com" not in final:
            # 有些短链跳转需要手动从 Location 里抠 BV 号
            m = re.search(r"(BV[0-9A-Za-z]{10})", r.text or "")
            if m:
                final = f"https://www.bilibili.com/video/{m.group(1)}/"
        return final
    except Exception:
        return url


def bilibili_meta(bvid: str) -> dict:
    """B站公开API取元数据（无需登录）。"""
    r = requests.get(
        "https://api.bilibili.com/x/web-interface/view",
        params={"bvid": bvid},
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
        timeout=20,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站元数据接口失败: code={data.get('code')} {data.get('message')}")
    d = data["data"]
    return {
        "title": d.get("title", ""),
        "uploader": (d.get("owner") or {}).get("name", ""),
        "duration": d.get("duration", 0),
        "aid": d.get("aid"),
        "cid": (d.get("pages") or [{}])[0].get("cid"),
        "desc": d.get("desc", ""),
        "pubdate": d.get("pubdate"),
    }


def probe_meta(url: str, cookies_browser: str = "") -> dict:
    """通用元数据探测：yt-dlp 读信息不下载。失败则退回网页 og:title。"""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(_ytdlp_opts(url, cookies_browser)) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title": info.get("title") or "",
            "uploader": info.get("uploader") or info.get("channel") or info.get("creator") or "",
            "duration": int(info.get("duration") or 0),
            "webpage_url": info.get("webpage_url") or url,
        }
    except Exception as e:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', r.text)
            title = m.group(1) if m else os.path.basename(url)
            return {"title": title, "uploader": "", "duration": 0, "webpage_url": url,
                    "probe_error": str(e)}
        except Exception as e2:
            return {"title": os.path.basename(url), "uploader": "", "duration": 0,
                    "webpage_url": url, "probe_error": f"{e} / {e2}"}


_BUID = f"{str(uuid.uuid4()).upper()}65185infoc"  # 每次启动随机生成的匿名设备指纹，仅用于接口风控


def bilibili_direct_audio(bvid: str, outdir: str, sessdata: str = "") -> str:
    """yt-dlp 被风控(412)时的兜底：直连 playurl API 下载音频流。返回 media.m4s 路径。"""
    cookie = f"SESSDATA={sessdata}; buvid3={_BUID}" if sessdata else f"buvid3={_BUID}"
    H = {"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": cookie}
    v = requests.get("https://api.bilibili.com/x/web-interface/view",
                     params={"bvid": bvid}, headers=H, timeout=20).json()["data"]
    p = requests.get("https://api.bilibili.com/x/player/playurl",
                     params={"bvid": bvid, "cid": v["cid"], "fnval": 16},
                     headers=H, timeout=20).json()
    if p.get("code") != 0:
        raise RuntimeError(f"playurl 失败: {p.get('code')} {p.get('message')}")
    audio = max(p["data"]["dash"]["audio"], key=lambda a: (a.get("bandwidth") or 0))
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "media.m4s")
    with requests.get(audio["baseUrl"], headers=H, stream=True, timeout=90) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def download_media(url: str, outdir: str, cookies_browser: str = "", sessdata: str = "") -> str:
    """下载音频优先的媒体文件，返回落盘路径。faster-whisper 可直接解码音/视频容器。"""
    import yt_dlp

    os.makedirs(outdir, exist_ok=True)
    opts = _ytdlp_opts(url, cookies_browser)
    if sessdata:  # 登录态降低风控概率，并解锁更高音质
        opts["http_headers"]["Cookie"] = f"SESSDATA={sessdata}"
    opts.update({
        "format": "bestaudio/best",
        "outtmpl": os.path.join(outdir, "media.%(ext)s"),
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    for name in sorted(os.listdir(outdir)):
        if name.startswith("media."):
            return os.path.join(outdir, name)
    raise RuntimeError("下载完成但未找到媒体文件")

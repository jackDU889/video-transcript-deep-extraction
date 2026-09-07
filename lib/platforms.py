"""平台识别：根据输入判断来源平台类型。"""
import os
import re
from dataclasses import dataclass

BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
BILI_HOST_RE = re.compile(r"(bilibili\.com|b23\.tv)", re.I)
DOUYIN_RE = re.compile(r"(douyin\.com|iesdouyin\.com|v\.douyin\.com)", re.I)
WECHAT_RE = re.compile(r"(channels\.weixin\.qq\.com|视频号)", re.I)

LOCAL_EXTS = {
    ".mp4", ".mov", ".mkv", ".flv", ".avi", ".webm", ".m4v", ".ts",
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus",
}


@dataclass
class Source:
    kind: str        # bilibili / douyin / wechat_channels / local
    url: str = ""    # 平台为页面/视频链接；local 为文件路径
    bvid: str = ""   # 仅 bilibili：BV号
    path: str = ""   # 仅 local：本地文件路径


def detect(source: str) -> Source:
    """识别输入是链接还是本地文件，以及属于哪个平台。"""
    s = source.strip().strip('"').strip("'")

    if os.path.isfile(s):
        ext = os.path.splitext(s)[1].lower()
        if ext and ext not in LOCAL_EXTS:
            raise SystemExit(f"[错误] 本地文件扩展名 {ext} 不是常见音视频格式：{s}")
        return Source(kind="local", url=s, path=s)

    if BILI_HOST_RE.search(s):
        m = BVID_RE.search(s)
        return Source(kind="bilibili", url=s, bvid=m.group(1) if m else "")

    if DOUYIN_RE.search(s):
        return Source(kind="douyin", url=s)

    if WECHAT_RE.search(s):
        return Source(kind="wechat_channels", url=s)

    raise SystemExit(
        "[错误] 无法识别的输入：" + s +
        "\n支持：B站视频链接（含 b23.tv 短链）、抖音链接（含 v.douyin.com 短链）、"
        "微信视频号链接（当前请改用本地视频文件）、本地音视频文件路径。"
    )

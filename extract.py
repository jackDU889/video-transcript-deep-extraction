#!/usr/bin/env python3
"""视频文案深度提取 CLI。

用法:
    python3 extract.py <B站/抖音链接或本地音视频文件> [选项]

选项:
    --model large-v3           whisper 模型（默认 large-v3，可改 medium 提速）
    --no-ts                    不在段首加 [mm:ss] 导航时间戳
    --sessdata XXX             B站登录态（优先级高于环境变量/文件）
    --cookies-from-browser NAME  让 yt-dlp 从指定浏览器读登录态（chrome/edge/safari/brave/firefox）
    --out-root DIR             输出根目录（默认: 脚本所在目录/输出）

流程: 字幕优先（B站需登录态）→ 下载音频 → 本地 whisper 转写 → 100%原文分段（程序硬校验）。
产出: <输出目录>/01_原始文案.md、meta.json、原始素材/
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import asr, diarize, fetch, platforms, segment, subtitles  # noqa: E402


def load_sessdata(cli_val: str) -> str:
    if cli_val:
        return cli_val.strip()
    env = os.environ.get("BILIBILI_SESSDATA", "").strip()
    if env:
        return env
    path = os.path.expanduser("~/.bilibili_sessdata")
    if os.path.isfile(path):
        return open(path).read().strip()
    return ""


def slugify(s: str, limit: int = 24) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s\u3000]+", "_", s or "").strip("_")
    return s[:limit] or "video"


def fmt_duration(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description="视频文案深度提取（100%原文 + 智能分段）")
    ap.add_argument("source", help="视频链接（B站/抖音）或本地音视频文件路径")
    ap.add_argument("--engine", default="auto", choices=["auto", "mlx", "fw"],
                    help="转写引擎：auto=本地mlx权重优先（默认）/ mlx=强制GPU / fw=强制CPU")
    ap.add_argument("--model", default="large-v3", help="faster-whisper 模型（fw 引擎用）")
    ap.add_argument("--mlx-model", default="whisper-large-v3-turbo-mlx", help="mlx 模型目录名")
    ap.add_argument("--diarize", default="auto", choices=["auto", "on", "off"],
                    help="说话人分离：auto=模型可用时自动（默认）/ on=强制（失败即退出）/ off=关闭")
    ap.add_argument("--num-speakers", type=int, default=0, help="强制说话人数量（0=自动估计）")
    ap.add_argument("--diarize-threshold", type=float, default=0.6,
                    help="声纹聚类合并阈值（真人0.5~0.7；说话人声音相近时调低如0.45）")
    ap.add_argument("--series", default="", help="所属系列名（批量任务传入，写入meta供分析队列选用视角）")
    ap.add_argument("--no-ts", action="store_true", help="段首不加导航时间戳")
    ap.add_argument("--sessdata", default="", help="B站 SESSDATA 登录态")
    ap.add_argument("--cookies-from-browser", dest="cookies_browser", default="",
                    help="从浏览器读登录态: chrome/edge/safari/brave/firefox")
    ap.add_argument("--out-root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "输出"))
    args = ap.parse_args()

    # 支持【标题】+链接 整行粘贴：自动抽取纯URL
    if not os.path.isfile(args.source.strip()):
        m = re.search(r"https?://[^\s，。,；;、】」\"']+", args.source)
        if m:
            args.source = m.group(0)

    src = platforms.detect(args.source)
    today = datetime.date.today().strftime("%Y%m%d")
    sessdata = load_sessdata(args.sessdata)

    meta = {"platform": src.kind, "source_input": src.url}
    segments = None
    transcribe_source = ""
    media_path = ""

    if src.kind == "bilibili":
        real_url = fetch.resolve_short_url(src.url)
        m = re.search(r"(BV[0-9A-Za-z]{10})", real_url)
        bvid = m.group(1) if m else src.bvid
        if not bvid:
            raise SystemExit("[错误] 未能从链接解析出 BV 号")
        info = fetch.bilibili_meta(bvid)
        meta.update({"url": real_url, "bvid": bvid, "aid": info["aid"], "cid": info["cid"],
                     "title": info["title"], "uploader": info["uploader"],
                     "duration": info["duration"], "desc": info["desc"]})
        print(f"[信息] {info['title']} | UP主 {info['uploader']} | 时长 {fmt_duration(info['duration'])}")

        if sessdata:
            print("[字幕] 检测到登录态，尝试获取B站官方/AI字幕…")
            try:
                segments = subtitles.get_bili_subtitles(bvid, info["aid"], info["cid"], sessdata)
                transcribe_source = "bilibili_subtitle" if segments else ""
                if segments:
                    print(f"[字幕] 获取成功：{len(segments)} 条")
                else:
                    print("[字幕] 接口返回无字幕，转 whisper 转写。")
            except Exception as e:
                print(f"[字幕] 获取失败（{e}），转 whisper 转写。")
        else:
            print("[字幕] 未提供B站登录态（SESSDATA），直接使用 whisper 转写。")

        if segments is None:
            media_dir = out_dir(args, today, meta) + "/原始素材"
            media_path = existing_media(media_dir)
            if media_path:
                print(f"[下载] 复用已有媒体文件: {media_path}")
            else:
                try:
                    media_path = fetch.download_media(real_url, media_dir, args.cookies_browser,
                                                      sessdata=sessdata)
                except Exception as e:
                    print(f"[下载] yt-dlp失败（{str(e)[:100]}），改用直连API下载音频…")
                    media_path = fetch.bilibili_direct_audio(bvid, media_dir, sessdata)
    elif src.kind == "douyin":
        real_url = fetch.resolve_short_url(src.url)
        info = fetch.probe_meta(real_url, args.cookies_browser)
        meta.update({"url": real_url, "title": info["title"], "uploader": info["uploader"],
                     "duration": info.get("duration", 0)})
        if info.get("probe_error"):
            print(f"[警告] 元数据探测异常: {info['probe_error']}")
        print(f"[信息] {meta['title']} | {meta.get('uploader', '')}")
        media_dir = out_dir(args, today, meta) + "/原始素材"
        media_path = existing_media(media_dir)
        if media_path:
            print(f"[下载] 复用已有媒体文件: {media_path}")
        else:
            media_path = fetch.download_media(real_url, media_dir, args.cookies_browser)
    elif src.kind == "wechat_channels":
        raise SystemExit(
            "[提示] 微信视频号网页端需扫码登录，暂无稳定的免登录抓取通道。\n"
            "请用以下任一方式提供视频文件，转写质量与其他平台完全一致：\n"
            "  1) 手机微信中打开视频号 → 长按/分享保存，或录屏导出；\n"
            "  2) 电脑端微信打开视频号视频，保存文件。\n"
            "然后运行: python3 extract.py <视频文件路径>"
        )
    else:  # local
        meta.update({"url": os.path.abspath(src.path), "title": os.path.splitext(os.path.basename(src.path))[0],
                     "uploader": ""})
        media_path = src.path

    # ---- 转写（字幕优先，否则 whisper）----
    asr_media_sec = 0.0
    if segments is None:
        if not media_path:
            raise SystemExit("[错误] 无媒体文件可转写")
        segments, asr_media_sec, engine_used = asr.transcribe(
            audio_path=media_path, engine=args.engine,
            fw_model=args.model, mlx_model=args.mlx_model)
        transcribe_source = f"mlx_{args.mlx_model}" if engine_used == "mlx" else f"whisper_{args.model}"

    if not segments:
        raise SystemExit("[错误] 转写结果为空，请检查音轨或链接有效性")

    # ---- 完整性检查：媒体时长必须与平台标注一致；转写时间轴必须覆盖到结尾 ----
    media_sec = media_duration(media_path) or asr_media_sec or float(meta.get("duration") or 0)
    meta_dur = float(meta.get("duration") or 0)
    if not meta_dur and media_sec:
        meta["duration"] = int(media_sec)  # 本地文件无平台时长，用实测值
    if media_sec and meta_dur and abs(media_sec - meta_dur) > 5:
        raise SystemExit(
            f"[完整性][严重] 媒体时长 {media_sec:.1f}s 与平台标注 {meta_dur:.0f}s 相差超过5秒，"
            "疑似下载不完整，拒绝产出转录稿。请删除 原始素材/ 下媒体文件后重试。")
    print(f"[完整性] 媒体时长 {media_sec:.1f}s / 平台标注 {meta_dur:.0f}s ✔" if media_sec
          else f"[完整性] 使用字幕路径，平台标注时长 {meta_dur:.0f}s")

    first_speech = segments[0][0]
    last_speech = max(s[1] for s in segments)
    speech_sec = sum(s[1] - s[0] for s in segments)
    ref = media_sec or meta_dur
    if ref and ref - last_speech > 90:
        print(f"[完整性][警告] 转写末句止于 {last_speech:.0f}s，距结尾仍有 {ref - last_speech:.0f}s —— "
              "若片尾并非音乐/彩蛋，请人工回听核查！")
    else:
        print(f"[完整性] 转写时间轴覆盖至 {last_speech:.0f}s / {ref:.0f}s，有效语音 {speech_sec:.0f}s ✔")

    # ---- 说话人分离（可选；字幕来源无音频时自动跳过，不影响主流程）----
    speaker_count = 0
    if args.diarize != "off" and media_path:
        if diarize.available() or args.diarize == "on":
            print("[说话人] 声纹分离中，请稍候…")
            try:
                diar_segs, n_spk = diarize.diarize(
                    media_path, os.path.dirname(media_path), num_speakers=args.num_speakers,
                    threshold=args.diarize_threshold)
                if n_spk > 1:
                    segments = diarize.assign_speakers(segments, diar_segs)
                    speaker_count = n_spk
                    print(f"[说话人] 识别到 {n_spk} 个说话人，段落将按说话人分段并标注。")
                else:
                    print("[说话人] 识别为单说话人，无需标注。")
            except Exception as e:
                if args.diarize == "on":
                    raise
                print(f"[说话人] 分离失败（{e}），继续不标注。")
        else:
            print("[说话人] 未找到声纹模型（模型/speaker-diarization/），跳过标注；--diarize on 可强制。")
    elif args.diarize != "off":
        print("[说话人] 字幕来源无音频文件，无法声纹分离。")

    # ---- 分段 + 硬校验 ----
    paras = segment.group_paragraphs(segments)
    segment.verify(paras, segments)
    print(f"[校验] 通过：{len(segments)} 条转写片段 → {len(paras)} 个段落，逐字一致。")

    # ---- 落盘 ----
    outdir = out_dir(args, today, meta)
    os.makedirs(outdir, exist_ok=True)
    meta.update({
        "transcribe_source": transcribe_source,
        "series": args.series or None,
        "speakers": speaker_count or None,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": outdir,
        "paragraphs": len(paras),
        "chars": sum(len(p[2]) for p in paras),
        "coverage": {
            "media_sec": round(media_sec, 1) if media_sec else None,
            "first_speech_sec": round(first_speech, 1),
            "last_speech_sec": round(last_speech, 1),
            "speech_sec": round(speech_sec, 1),
            "tail_gap_sec": round(ref - last_speech, 1) if ref else None,
        },
    })
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(outdir, "原始素材", "raw_segments.json"), "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False)

    header = [
        f"# {meta.get('title', '视频')} · 原始文案",
        "",
        f"- 来源：{meta['platform']}  {meta.get('url', '')}",
        f"- UP主/作者：{meta.get('uploader', '') or '（未知）'}",
        f"- 时长：{fmt_duration(meta.get('duration') or 0)}",
        f"- 转写方式：{transcribe_source}",
    ]
    if speaker_count:
        header.append(f"- 说话人：{speaker_count} 人（程序按声纹聚类自动标注【说话人N】，匿名标签，重叠/短插话边界可能有误差）")
    header += [
        f"- 生成时间：{meta['created_at']}",
        "- 说明：以下为视频语音的**完整逐字转录**，未做任何改写、删减或标点修改；"
        "仅按语音停顿划分段落，段首 `[mm:ss]` 为该段起始时间（仅用于回听定位，不属于正文）。",
        "",
        "---",
        "",
    ]
    with open(os.path.join(outdir, "01_原始文案.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(header) + segment.render_md(
            paras, with_ts=not args.no_ts, with_speakers=speaker_count > 0) + "\n")

    print(f"[完成] {outdir}/01_原始文案.md（{meta['chars']} 字 / {len(paras)} 段）")
    print(f"[下一步] 深度学习导读: python3 analyze.py \"{outdir}\"")


def media_duration(path: str) -> float:
    """ffprobe 读取媒体实际时长（秒）；失败返回 0。"""
    if not path or not os.path.isfile(path):
        return 0.0
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def existing_media(media_dir: str) -> str:
    """复用已下载的媒体文件，避免重复请求触发平台风控。"""
    if os.path.isdir(media_dir):
        for name in sorted(os.listdir(media_dir)):
            if name.startswith("media."):
                return os.path.join(media_dir, name)
    return ""


def out_dir(args, today: str, meta: dict) -> str:
    name = f"{today}_{slugify(meta.get('title', ''))}"
    if meta.get("uploader"):
        name += f"_{slugify(meta['uploader'], 16)}"
    path = os.path.join(args.out_root, name)
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "原始素材"), exist_ok=True)
    return path


if __name__ == "__main__":
    main()

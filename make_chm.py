#!/usr/bin/env python3
"""把 输出/ 下所有视频的深度学习导读（及原始文案）编译为 CHM 电子书。

- 按 meta.json 的 series 分组（5系列 + 其他），树形目录：系列 → 视频 → 导读/原始文案
- 遵循《经验记录-XMind整理CHM电子书》定论：所有文本文件 UTF-8 BOM + http-equiv 声明；
  hhp 用 Default Topic、Full-text search=Yes、Binary Index=No
- 可反复执行：每次重新扫描，自动纳入新完成的视频（"动态"=随进度重编即更新）

用法: .venv/bin/python make_chm.py   → 输出/老韩一米九视频导读.chm
"""
import glob
import html
import json
import os
import re
import shutil

import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HERE, "输出")
BUILD = os.path.join(OUT_ROOT, "chm_build")
CHM_PATH = os.path.join(OUT_ROOT, "老韩一米九视频导读.chm")
BOOK_TITLE = "老韩一米九 · 视频深度学习导读全集"

SERIES_ORDER = ["企业IT方法论与最佳实践", "韩工开物", "一家不平何以平天", "有意思的访谈",
                "运营商大起底", "其他"]

MD = markdown.Markdown(extensions=["tables", "fenced_code", "toc"])

CSS = """
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:860px;
margin:0 auto;padding:24px 20px 60px;line-height:1.75;color:#222}
h1{font-size:1.5em;border-bottom:2px solid #2f6fad;padding-bottom:8px}
h2{font-size:1.2em;border-left:4px solid #2f6fad;padding-left:10px;margin-top:1.6em}
table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{border:1px solid #ccc;padding:6px 10px;text-align:left}
th{background:#f0f4f8}
blockquote{border-left:4px solid #bbb;margin:10px 0;padding:4px 14px;color:#555;background:#f8f8f8}
code{background:#f2f2f2;padding:1px 5px;border-radius:3px}
.meta{color:#666;font-size:.9em;background:#f6f8fa;border:1px solid #e1e4e8;
border-radius:6px;padding:10px 14px;margin:14px 0}
.meta a{color:#2f6fad}
.nav{margin:18px 0;font-size:.95em}
.nav a{color:#2f6fad;text-decoration:none;margin-right:14px}
.toc-list{line-height:2}
.series-h{background:#eef3f8;padding:6px 12px;border-radius:6px}
"""


def bom(text: str) -> str:
    return "\ufeff" + text


def html_page(title: str, body: str) -> str:
    return bom(
        '<!DOCTYPE html>\n<html><head>\n'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">\n'
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n"
        "</head><body>\n" + body + "\n</body></html>\n"
    )


def md_to_html(md_text: str) -> str:
    MD.reset()
    return MD.convert(md_text)


def load_videos():
    """扫描输出目录，返回 [{meta, dir, has_02, has_01, draft_only}]"""
    vids = []
    for mp in sorted(glob.glob(os.path.join(OUT_ROOT, "*", "meta.json"))):
        d = os.path.dirname(mp)
        try:
            meta = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        if not meta.get("bvid"):
            continue
        vids.append({
            "meta": meta,
            "dir": d,
            "has_02": os.path.isfile(os.path.join(d, "02_深度学习导读.md")),
            "has_01": os.path.isfile(os.path.join(d, "01_原始文案.md")),
            "draft_only": os.path.isfile(os.path.join(d, "02_深度学习导读_API初稿.md"))
                          and not os.path.isfile(os.path.join(d, "02_深度学习导读.md")),
        })
    return vids


def fmt_dur(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def main():
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)

    vids = load_videos()
    done = [v for v in vids if v["has_02"]]
    draft = [v for v in vids if v["draft_only"]]
    print(f"扫描到 {len(vids)} 个视频；导读完成 {len(done)}（其中仅初稿 {len(draft)}）")

    # 按系列分组（首属系列），组内按发布时间降序
    groups = {}
    for v in vids:
        s = v["meta"].get("series") or "其他"
        groups.setdefault(s if s in SERIES_ORDER else "其他", []).append(v)
    for g in groups.values():
        g.sort(key=lambda v: -(v["meta"].get("pubdate") or 0))

    series_list = [s for s in SERIES_ORDER if s in groups] + \
                  [s for s in groups if s not in SERIES_ORDER]

    entries = []  # (系列, 视频标题, [页面...])  页面 = (文件名, 条目名)
    stats = {}

    def series_of(v):
        return v["meta"].get("series") or "其他"

    for si, series in enumerate(series_list):
        page_no = si * 1000
        for v in groups[series]:
            meta = v["meta"]
            seq = meta["bvid"]
            pages = []
            nav = []
            if v["has_02"]:
                fn02 = f"p{page_no}_02_{seq}.html"
                md02 = open(os.path.join(v["dir"], "02_深度学习导读.md"), encoding="utf-8").read()
                note = ""
                if v["draft_only"]:
                    note = '<div class="meta">⚠ 本文为 API 自动初稿，人工精修版待更新。</div>'
                body = (f'<div class="meta">系列：{html.escape(series)}｜'
                        f'时长 {fmt_dur(meta.get("duration"))}｜'
                        f'来源：<a href="https://www.bilibili.com/video/{meta["bvid"]}" target="_blank">B站 {meta["bvid"]}</a>｜'
                        f'生成：{html.escape(str(meta.get("created_at", "")))}</div>' + note)
                body += md_to_html(md02)
                open(os.path.join(BUILD, fn02), "w", encoding="utf-8").write(
                    html_page(f"{meta['title']} · 深度学习导读", body))
                pages.append((fn02, "📖 深度学习导读"))
                page_no += 1
            if v["has_01"]:
                fn01 = f"p{page_no}_01_{seq}.html"
                md01 = open(os.path.join(v["dir"], "01_原始文案.md"), encoding="utf-8").read()
                body = md_to_html(md01)
                open(os.path.join(BUILD, fn01), "w", encoding="utf-8").write(
                    html_page(f"{meta['title']} · 原始文案", body))
                pages.append((fn01, "📝 原始文案"))
                page_no += 1
            if pages:
                entries.append((series, meta["title"], pages, meta))
                stats[series] = stats.get(series, 0) + 1

    # ---------- index.html ----------
    total_done = sum(stats.values())
    idx = [f"<h1>{BOOK_TITLE}</h1>",
           f'<div class="meta">共收录 {total_done} 个视频的深度学习导读'
           f'（全空间 {len(vids)} 个，其余随批量进度更新）｜生成时间：'
           f'{__import__("time").strftime("%Y-%m-%d %H:%M")}</div>',
           '<p>左侧目录按系列分组；顶部「索引」标签可按标题检索，「搜索」支持全文检索。</p>']
    for series in series_list:
        vids_in = [e for e in entries if e[0] == series]
        if not vids_in:
            continue
        idx.append(f'<h2 class="series-h">{html.escape(series)}（{len(vids_in)}）</h2><ul class="toc-list">')
        for _s, title, pages, meta in vids_in:
            first = pages[0][0] if pages else "index.html"
            idx.append(f'<li><a href="{first}">{html.escape(title)}</a>'
                       f' <span style="color:#888">（{fmt_dur(meta.get("duration"))}）</span></li>')
        idx.append("</ul>")
    open(os.path.join(BUILD, "index.html"), "w", encoding="utf-8").write(
        html_page(BOOK_TITLE + " · 目录", "\n".join(idx)))

    # ---------- .hhc 目录（系列 → 视频 → 导读/原文）----------
    hhc = ['<!DOCTYPE html PUBLIC "-//IETF//DTD HTML//EN">',
           '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head><body>',
           '<ul>']
    for series in series_list:
        vids_in = [e for e in entries if e[0] == series]
        if not vids_in:
            continue
        hhc.append('<li><object type="text/site properties">'
                   f'<param name="ImageNumber" value="1"></object>'
                   f'<object type="text/sitemap"><param name="Name" value="{html.escape(series)}">'
                   f'<param name="Local" value="{vids_in[0][2][0][0]}"></object>'
                   '<ul>')
        for _s, title, pages, _m in vids_in:
            hhc.append(f'<li><object type="text/sitemap"><param name="Name" value="{html.escape(title)}">'
                       f'<param name="Local" value="{pages[0][0]}"></object><ul>')
            for fn, name in pages:
                hhc.append(f'<li><object type="text/sitemap"><param name="Name" value="{html.escape(name)}">'
                           f'<param name="Local" value="{fn}"></object></li>')
            hhc.append('</ul></li>')
        hhc.append('</ul></li>')
    hhc.append('</ul></body></html>')
    open(os.path.join(BUILD, "toc.hhc"), "w", encoding="utf-8").write(bom("\n".join(hhc)))

    # ---------- .hhk 索引 ----------
    hhk = ['<!DOCTYPE html PUBLIC "-//IETF//DTD HTML//EN">',
           '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head><body>', '<ul>']
    for _s, title, pages, _m in entries:
        hhk.append(f'<li><object type="text/sitemap"><param name="Name" value="{html.escape(title)}">'
                   f'<param name="Local" value="{pages[0][0]}"></object></li>')
    hhk.append('</ul></body></html>')
    open(os.path.join(BUILD, "index.hhk"), "w", encoding="utf-8").write(bom("\n".join(hhk)))

    # ---------- .hhp 工程 ----------
    hhp = [
        "[OPTIONS]", f"Title={BOOK_TITLE}", "Default Topic=index.html",
        "Full-text search=Yes", "Binary Index=No", "Language=0x804", "Compatibility=1.1",
        "Compiled File=老韩一米九视频导读.chm", "Contents file=toc.hhc",
        "Index file=index.hhk", "Error log file=build.log", "", "[FILES]", ""]
    for root, _dirs, files in os.walk(BUILD):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), BUILD).replace(os.sep, "/")
            if rel not in ("toc.hhc", "index.hhk") and not rel.endswith(".hhp"):
                hhp.append(rel)
    open(os.path.join(BUILD, "book.hhp"), "w", encoding="utf-8").write(bom("\n".join(hhp)))

    # ---------- 编译 ----------
    r = os.system(f'cd "{BUILD}" && chmcmd book.hhp > chmcmd.log 2>&1')
    out_chm = os.path.join(BUILD, "老韩一米九视频导读.chm")
    if r == 0 and os.path.isfile(out_chm) and os.path.getsize(out_chm) > 10000:
        shutil.move(out_chm, CHM_PATH)
        n_pages = sum(len(e[2]) for e in entries)
        n_guide = sum(1 for e in entries if any("02_" in p[0] for p in e[2]))
        size = os.path.getsize(CHM_PATH) / 1024 / 1024
        print(f"✅ CHM 已生成：{CHM_PATH}（{size:.1f} MB，收录 {len(entries)} 个视频，"
              f"其中导读成稿 {n_guide} 篇，共 {n_pages} 页）")
        print("   阅读器：CHM Assistant（已设默认）；重新生成命令：.venv/bin/python make_chm.py")
    else:
        print("❌ 编译失败，日志：")
        print(open(os.path.join(BUILD, "chmcmd.log")).read()[-800:])


if __name__ == "__main__":
    main()

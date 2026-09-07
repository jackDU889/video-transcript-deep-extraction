#!/usr/bin/env python3
"""批量文案深度提取：读取 CSV 中每一行的链接，逐个执行「提取→说话人分离→100%原文→分析初稿」。

CSV 格式（utf-8 / utf-8-sig，Excel/WPS 可直接编辑）：
    链接
    【谁来管住失控中的企业AI应用？】https://www.bilibili.com/video/BVxxxx
    【另一个视频】https://...

- 链接列：每行含 URL 即可识别，【标题】前缀可选（仅展示用，程序自动取真实标题）
- 状态、输出目录 两列由本脚本自动回写，无需手工填
- 已成功的行重复运行时自动跳过（幂等），失败行修正后重跑即可

用法:
    .venv/bin/python batch.py 批量任务.csv [--no-analyze] [--extract-args "..."]
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
URL_RE = re.compile(r"https?://[^\s，。,；;、】」\"']+")


def read_csv(path: str):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    if not rows:
        raise SystemExit(f"[错误] {path} 没有内容")
    header = [c.strip() for c in rows[0]]
    has_header = ("链接" in header or "url" in header[0].lower()) and not URL_RE.search(rows[0][0] or "")
    return (header if has_header else None), rows, has_header


def run_one(url: str, extract_args: list, do_analyze: bool):
    """跑单个视频，返回 (ok, outdir_or_msg)。"""
    cmd = [sys.executable, os.path.join(HERE, "extract.py"), url] + extract_args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200,
                       cwd=HERE, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"\[完成\] (.+?)/01_原始文案\.md", out)
    if r.returncode == 0 and m:
        outdir = m.group(1)
        if do_analyze:
            ra = subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"), outdir],
                                capture_output=True, text=True, timeout=1800, cwd=HERE)
            if ra.returncode != 0:
                return True, outdir + "（⚠ 分析初稿失败: " + (ra.stdout + ra.stderr)[-200:] + "）"
        return True, outdir
    err = ""
    for line in out.splitlines():
        if "错误" in line or "Error" in line or "ERROR" in line:
            err = line.strip()[:160]
            break
    return False, err or out[-200:] or f"退出码 {r.returncode}"


def main():
    ap = argparse.ArgumentParser(description="批量视频文案深度提取")
    ap.add_argument("csv_path", help="任务CSV路径")
    ap.add_argument("--no-analyze", action="store_true", help="只提取文案，不生成分析初稿")
    ap.add_argument("--extract-args", default="",
                    help='透传给 extract.py 的参数，如 "--num-speakers 2 --no-ts"')
    args = ap.parse_args()

    header, rows, has_header = read_csv(args.csv_path)
    extract_args = args.extract_args.split() if args.extract_args else []
    n_ok = n_skip = n_fail = 0

    # 定位/创建 状态、输出目录 列
    if has_header:
        while len(header) < 3:
            header.append("")
        header[0] = header[0] or "链接"
        header[1] = "状态" if "状态" not in header else header[1]
        header[2] = "输出目录" if "输出目录" not in header else header[2]

    data_rows = rows[1:] if has_header else rows
    results = []
    for i, row in enumerate(data_rows, start=1):
        cell = next((c for c in row if URL_RE.search(c or "")), "")
        m = URL_RE.search(cell)
        if not m:
            results.append((row, "⏭ 跳过（无有效链接）", ""))
            n_skip += 1
            continue
        url = m.group(0).rstrip(")，。 ")
        # 幂等：此前已成功且输出目录仍有效则跳过
        if len(row) >= 2 and str(row[1]).startswith("✅") and len(row) >= 3 and os.path.isdir(row[2]):
            results.append((row, row[1], row[2]))
            n_skip += 1
            continue
        print(f"\n========== [{i}/{len(data_rows)}] {url} ==========", flush=True)
        ok, info = run_one(url, extract_args, not args.no_analyze)
        title_m = re.search(r"【(.+?)】", cell)
        label = title_m.group(1) if title_m else url
        status = ("✅ 完成 " + datetime.datetime.now().strftime("%m-%d %H:%M")) if ok else ("❌ 失败：" + info)
        outdir = info if ok else ""
        print(f"---------- {'✅' if ok else '❌'} {label} ----------", flush=True)
        new_row = list(row) + ["", "", ""]
        new_row[0] = cell
        new_row[1] = status
        new_row[2] = outdir
        results.append((new_row, status, outdir))
        n_ok += ok
        n_fail += not ok

    # 回写 CSV（保留表头）
    with open(args.csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if has_header:
            w.writerow(header)
        for row, _s, _o in results:
            w.writerow(row)

    print(f"\n[批量完成] 成功 {n_ok} | 跳过 {n_skip} | 失败 {n_fail}")
    for row, status, outdir in results:
        if "❌" in status:
            print(f"  失败行: {row[0][:60]} → {status}")


if __name__ == "__main__":
    main()

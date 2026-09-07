#!/usr/bin/env python3
"""深度学习导读生成 CLI（API 自动初稿）。

用法:
    python3 analyze.py <extract.py 的输出目录> [--model glm-5.3-flash]

读取目录内 01_原始文案.md 与 meta.json，调用 LLM API 按「深度学习导读」七段框架
生成 02_深度学习导读.md；若同名文件已存在（例如人工精修版），则写入
02_深度学习导读_API初稿.md，绝不覆盖。
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import llm, segment  # noqa: E402

SYSTEM_PROMPT = """你是一位资深的知识导读作者，擅长把视频口播逐字稿转化为帮助读者"真正学进去"的深度导读文章。你的读者想通过阅读你的文章深入理解视频内容并学到知识，而不是只看摘要。

写作铁律：
1. 忠于逐字稿：所有对视频内容的转述必须有逐字稿依据，不虚构、不脑补视频里没有的信息。
2. 区分性质：明确标注哪些是客观事实、哪些是UP主的个人观点/体验、哪些是推测；二手转述的案例要标注[二手转述]。
3. 补齐背景：视频默认听众已经懂的概念，你要在"背景知识补齐"里讲透，让没有基础的读者也能读懂。背景部分允许使用视频之外的公开知识补充（标准、行业格局、技术原理），但必须与视频观点区分开。
4. 批判并存：不吹不黑，指出视频立场的可能偏向与未讲到的另一面。
5. 可溯源：逐字稿段首的 [mm:ss] 是**真实时间戳**，直接在结论和引用处标注（如 [08:50]），不要自行推算或编造时间；引用逐字稿原文时使用 > 引用块并保持原文一字不改。
6. 多说话人：逐字稿段落可能带【说话人N】前缀（程序声纹聚类的匿名标签）。若存在多个说话人，论证拆解与批判视角必须区分各说话人的观点、立场与可信度，不得混为一谈；若为单说话人或无标注，忽略该标签。
7. 语言：简体中文，全文 3000~6000 字，直接输出文章正文（不要输出额外说明），以文章一级标题开头。

文章必须严格包含以下七个章节（Markdown 二级标题）：

## 一、一图速览
3~5 条核心结论，每条一句话概括 + 一行依据说明，用列表呈现。先给答案，让读者30秒抓住全片。

## 二、背景知识补齐
逐个科普视频中出现的关键概念/产品/名词（读者可能不熟悉的每个都要覆盖），说明它是什么、为什么重要、在本次视频语境中扮演什么角色。

## 三、论证脉络拆解
梳理UP主的叙事与论证结构：如何开场、提出什么问题、给出什么论据、得出什么结论。用"论点→论据→结论"的链条呈现，并对每条主张标注性质：[事实] / [个人观点或体验] / [推测]。

## 四、批判性视角
分析UP主可能的立场偏向（利益相关、样本局限、评测方法是否严谨等），指出视频没讲到的另一面，并给出读者交叉验证的具体做法。

## 五、实践落地建议
把视频内容转化为对读者的可操作建议：什么情况下该参考这个结论、做决策前还应该确认什么。

## 六、延伸学习路径
按"入门→进阶"给出关键词清单、值得检索的问题、相关的标准/规范/资料类型，帮读者继续深入学习。

## 七、术语表
用表格（术语 | 解释 | 在本视频中的含义）汇总全文术语。

之后追加一个补充章节：

## 附注：逐字稿同音字对照
逐字稿是语音转写产物，存在同音错字（如品牌名音译、"终端"写成"中端"等）。通读逐字稿，把影响理解的同音字噪声整理成对照表（逐字稿写法 | 实际含义），供读者对照阅读原文。"""

# 分系列差异化分析视角（叠加在七段框架之上）
SERIES_ANGLES = {
    "企业IT方法论与最佳实践": (
        "本系列是企业IT方法论内容，分析侧重【方法论提炼】：\n"
        "- 三、论证脉络拆解：重点还原「问题场景→原则→具体做法→效果」的因果链；\n"
        "- 五、实践落地建议：改写为可直接执行的检查清单或流程步骤，标注前提条件与适用边界；\n"
        "- 六、延伸学习：围绕可复用的框架、工具与决策模型展开。"),
    "韩工开物": (
        "本系列是产品/技术深度内容，分析侧重【技术原理拆解】：\n"
        "- 二、背景知识补齐：按「工作原理→关键参数→设计权衡」讲透，可补充公开标准与技术资料；\n"
        "- 三、论证脉络拆解：关注实测/实验设计与数据的可信度；\n"
        "- 五、实践落地建议中增加「动手验证思路」：读者如何自己复现或验证视频结论。"),
    "一家不平何以平天": (
        "本系列是行业现象评述内容，分析侧重【观点评述】：\n"
        "- 三、论证脉络拆解：强制做「UP主观点 vs 可能的反方观点」对照呈现；\n"
        "- 四、批判性视角：加重分析立场与利益相关、情绪化表达与事实的边界；\n"
        "- 结论落在：读者应如何基于这些信息形成自己的独立判断。"),
    "有意思的访谈": (
        "本系列是访谈对话内容，分析侧重【对话萃取】：\n"
        "- 严格按【说话人N】标签归属每位发言者的核心主张与论据，不得混淆；\n"
        "- 三、论证脉络拆解以「人物→主张→依据」结构呈现；\n"
        "- 摘录3~5条金句或关键问答（> 引用块 + 时间戳）；\n"
        "- 四、批判性视角：评估各说话人的立场、专业背景与可信度。"),
    "运营商大起底": (
        "本系列是运营商/宽带行业内容，分析侧重【行业机制揭秘】：\n"
        "- 二、背景知识补齐：讲清运营商的资费结构、网络架构与商业逻辑；\n"
        "- 三、论证脉络拆解：关注「机制→对用户的实际影响」链条；\n"
        "- 五、实践落地建议：落到「怎么选、怎么谈、怎么避坑」的具体动作。"),
}



def main() -> None:
    ap = argparse.ArgumentParser(description="按七段框架生成深度学习导读（API 自动初稿）")
    ap.add_argument("outdir", help="extract.py 的输出目录（含 01_原始文案.md 与 meta.json）")
    ap.add_argument("--model", default=llm.DEFAULT_MODEL,
                    help=f"LLM 模型（默认 {llm.DEFAULT_MODEL}，可用 origin-deepseek-v4-pro 等）")
    args = ap.parse_args()

    outdir = os.path.abspath(args.outdir)
    md_path = os.path.join(outdir, "01_原始文案.md")
    meta_path = os.path.join(outdir, "meta.json")
    if not os.path.isfile(md_path) or not os.path.isfile(meta_path):
        raise SystemExit(f"[错误] {outdir} 缺少 01_原始文案.md 或 meta.json，请先运行 extract.py")

    meta = json.load(open(meta_path, encoding="utf-8"))
    # 保留段首 [mm:ss] 与【说话人N】标注喂给模型：时间戳真实可引用，说话人用于归属
    md = open(md_path, encoding="utf-8").read()
    transcript = md.split("\n---\n", 1)[1].strip() if "\n---\n" in md else md
    m, s = divmod(int(meta.get("duration") or 0), 60)
    dur = f"{m}分{s:02d}秒" if meta.get("duration") else "未知时长"

    user = (
        f"请基于以下视频逐字稿撰写深度学习导读文章。\n\n"
        f"视频信息：\n"
        f"- 标题：{meta.get('title', '')}\n"
        f"- 作者/UP主：{meta.get('uploader', '') or '未知'}\n"
        f"- 时长：{dur}\n"
        f"- 平台：{meta.get('platform', '')}\n"
        f"- 系列：{meta.get('series') or '其他'}\n\n"
        f"逐字稿全文：\n\n{transcript}"
    )

    series = meta.get("series") or ""
    system_prompt = SYSTEM_PROMPT
    if series in SERIES_ANGLES:
        system_prompt = SYSTEM_PROMPT + "\n\n" + SERIES_ANGLES[series]

    prov = llm.resolve_provider(args.model)
    print(f"[LLM] 系列「{series or '其他'}」视角 | 优先级链: {' → '.join(p['name'] for p in llm.resolve_chain(args.model))}")
    print(f"[LLM] 输入 {len(user)} 字符，生成中（可能需要1-3分钟）…")
    t0 = datetime.datetime.now()
    article, prov_used = llm.chat_auto(system_prompt, user, model=args.model)
    print(f"[LLM] 实际使用 {prov_used['name']} ({prov_used['model']})，"
          f"用时 {(datetime.datetime.now()-t0).seconds}s，输出 {len(article)} 字符")

    target = os.path.join(outdir, "02_深度学习导读.md")
    if os.path.exists(target):
        target = os.path.join(outdir, "02_深度学习导读_API初稿.md")
    with open(target, "w", encoding="utf-8") as f:
        f.write(f"<!-- 由 analyze.py 自动生成 | 模型: {prov['model']} | "
                f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n\n" + article + "\n")
    print(f"[完成] {target}")


if __name__ == "__main__":
    main()

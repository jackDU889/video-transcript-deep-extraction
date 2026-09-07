"""段落划分与一致性校验：只按停顿/长度重新分组，绝不改动任何文字。"""
import re

# 分段参数（可按需调整）
GAP_BREAK = 1.5     # 停顿 ≥1.5s 视为强断点
GAP_SOFT = 0.8      # 停顿 ≥0.8s 且段落已较长时视为弱断点
MIN_LEN = 30        # 强断点成立所需的最小段落长度（字）
SOFT_LEN = 120      # 弱断点成立所需的最小段落长度（字）
MAX_LEN = 240       # 段落长度硬上限，到达即在最近分句边界强制分段

_SENT_END = "。！？；…？！.!?"


def _ends_sentence(t: str) -> bool:
    return bool(t) and t[-1] in _SENT_END


def group_paragraphs(segments):
    """输入 [(start, end, text)] 或 [(start, end, text, speaker)]，输出段落列表。

    段落为 (start, end, text, speaker)。只在转写片段边界处分组，
    片段文字原样拼接，不做任何修改。说话人变化处强制分段（-1 表示未知，不触发）。
    """
    norm = []
    for s in segments:
        s = tuple(s)
        norm.append(s + (-1,) if len(s) == 3 else s)

    paras = []
    cur = []          # 当前段落内片段
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if cur:
            paras.append((cur[0][0], cur[-1][1], "".join(s[2] for s in cur), cur[0][3]))
            cur, cur_len = [], 0

    for seg in norm:
        st, en, tx, spk = seg
        if cur:
            gap = st - cur[-1][1]
            # 硬上限：到达 MAX_LEN 后在分句处强制分段；连续无标点也最多再宽容 60 字
            force = cur_len >= MAX_LEN and (
                _ends_sentence(cur[-1][2]) or cur_len >= MAX_LEN + 60
            )
            spk_change = spk != cur[-1][3] and (spk >= 0 or cur[-1][3] >= 0)
            if (
                spk_change
                or (gap >= GAP_BREAK and cur_len >= MIN_LEN)
                or (gap >= GAP_SOFT and cur_len >= SOFT_LEN and _ends_sentence(cur[-1][2]))
                or force
            ):
                flush()
        cur.append(seg)
        cur_len += len(tx)
    flush()
    return paras


def verify(paras, segments):
    """硬校验：段落拼接必须与原始转写逐字一致，不一致直接抛错拒绝产出。"""
    para_text = "".join(p[2] for p in paras)
    seg_text = "".join(s[2] for s in segments)
    if para_text != seg_text:
        # 定位第一个差异点，方便排查
        i = 0
        while i < min(len(para_text), len(seg_text)) and para_text[i] == seg_text[i]:
            i += 1
        raise AssertionError(
            f"[一致性校验失败] 段落拼接与原始转写在第 {i} 字处不一致：\n"
            f"  段落: …{para_text[max(0, i-20):i+20]}…\n"
            f"  原始: …{seg_text[max(0, i-20):i+20]}…"
        )


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_md(paras, with_ts: bool = True, with_speakers: bool = False) -> str:
    """渲染为正文：每段一行（段首可选【说话人N】与 [mm:ss] 导航时间戳），段间空行。

    说话人标签顺序：按首次出现顺序 说话人1/2/…，由调用方传入映射或使用默认排序。
    """
    lines = []
    for p in paras:
        st, _en, text = p[0], p[1], p[2]
        spk = p[3] if len(p) > 3 else -1
        prefix = ""
        if with_speakers and spk >= 0:
            prefix += f"【说话人{spk + 1}】"
        if with_ts:
            prefix += f"[{fmt_ts(st)}] "
        lines.append(prefix + text)
    return "\n\n".join(lines)


def strip_transcript(md_text: str) -> str:
    """从 01_原始文案.md 还原纯转写文本（去头部说明与段首时间戳，保留【说话人N】标注），供分析程序使用。"""
    body = md_text
    if "\n---\n" in md_text:
        body = md_text.split("\n---\n", 1)[1]
    lines = []
    for line in body.splitlines():
        t = re.sub(r"^\[[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?\]\s*", "", line.strip())
        if t:
            lines.append(t)
    return "\n\n".join(lines)

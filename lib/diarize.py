"""说话人分离（Speaker Diarization）：pyannote 分割 ONNX + 3D-Speaker Cam++ 声纹聚类。

基于 sherpa-onnx 离线流水线，模型本地缓存于 模型/speaker-diarization/，无需 HuggingFace。
输出匿名声纹标签（说话人1/2/…），按"时间重叠最大"归属到每条转写片段。
重叠说话与极短插话的边界存在固有限制，属行业普遍水平。
"""
import os
import subprocess

TASK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SD_MODEL_DIR = os.path.join(TASK_DIR, "模型", "speaker-diarization")

_SEGMENTATION_TAR = "pyannote-segmentation-3-0.tar.bz2"
_EMBEDDING_FILE = "campplus-zh.onnx"


def available() -> bool:
    seg = os.path.join(SD_MODEL_DIR, "sherpa-onnx-pyannote-segmentation-3-0", "model.onnx")
    emb = os.path.join(SD_MODEL_DIR, _EMBEDDING_FILE)
    return os.path.isfile(seg) and os.path.isfile(emb)


def _to_wav16k(media_path: str, wav_path: str) -> str:
    """ffmpeg 转 16kHz 单声道 wav（sherpa-onnx 要求）。文件名含源文件指纹，避免缓存串用。"""
    import hashlib

    st = os.stat(media_path)
    key = hashlib.md5(
        f"{os.path.abspath(media_path)}:{st.st_size}:{int(st.st_mtime)}".encode()
    ).hexdigest()[:10]
    wav_path = os.path.join(os.path.dirname(wav_path), f"media_16k_{key}.wav")
    if os.path.isfile(wav_path):
        return wav_path
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", media_path, "-ac", "1", "-ar", "16000", wav_path]
    subprocess.run(cmd, check=True, timeout=600)
    return wav_path


def _read_wav(path: str):
    """读 16-bit wav，返回 (sample_rate, float32 单声道采样)。"""
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        data = w.readframes(n)
    if sw != 2:
        raise RuntimeError(f"仅支持16-bit wav，当前 {sw*8}-bit")
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        samples = samples.reshape(-1, ch).mean(axis=1)
    return sr, samples


def diarize(media_path: str, wav_dir: str, num_speakers: int = 0, threshold: float = 0.6):
    """返回 (diar_segments, n_speakers)。diar_segments 为 [(start, end, speaker_idx)]。

    num_speakers=0 表示自动估计；>0 时强制簇数。
    threshold 为聚类合并阈值（余弦相似度）：调低可分出更多说话人，
    真人场景 0.5~0.7 常用；音色相近的音频可降到 0.45 左右。
    """
    if not available():
        raise RuntimeError(f"缺少模型文件，请检查 {SD_MODEL_DIR}")
    import sherpa_onnx

    wav = _to_wav16k(media_path, os.path.join(wav_dir, "media_16k.wav"))

    seg_model = os.path.join(SD_MODEL_DIR, "sherpa-onnx-pyannote-segmentation-3-0", "model.onnx")
    emb_model = os.path.join(SD_MODEL_DIR, _EMBEDDING_FILE)
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=seg_model)
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb_model),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers if num_speakers > 0 else -1,
            threshold=threshold,
        ),
        min_duration_off=0.3,
        min_duration_on=0.3,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    sr, samples = _read_wav(wav)
    if sr != sd.sample_rate:
        raise RuntimeError(f"采样率不符：wav={sr}，模型要求 {sd.sample_rate}")
    raw = sd.process(samples)

    if hasattr(raw, "num_segments"):  # 新版API：结果对象，经排序方法取段列表
        segments_list = raw.sort_by_start_time()
        out = [(round(float(s.start), 2), round(float(s.end), 2), int(s.speaker))
               for s in segments_list]
    else:  # 旧版API：可直接迭代
        out = []
        for s in raw:
            if hasattr(s, "start"):
                out.append((round(float(s.start), 2), round(float(s.end), 2), int(s.speaker)))
            else:
                out.append((round(float(s[0]), 2), round(float(s[1]), 2), int(s[2])))
    n = len({s[2] for s in out}) if out else 0
    out = _merge_minor_clusters(out)
    n_merged = len({s[2] for s in out}) if out else 0
    if n_merged != n:
        print(f"[说话人] 合并碎簇：{n} → {n_merged} 个说话人")
    return out, n_merged


def _merge_minor_clusters(diar_segs, ratio: float = 0.1, min_major_sec: float = 8.0):
    """合并"碎簇"：总时长过小的声纹簇（短插话/笑声导致的过度分裂）
    并入时间上前邻或后邻的主簇。ratio=碎簇时长上限占最大簇的比例。"""
    if not diar_segs:
        return diar_segs
    durs = {}
    for s, e, spk in diar_segs:
        durs[spk] = durs.get(spk, 0.0) + (e - s)
    max_dur = max(durs.values())
    majors = {spk for spk, d in durs.items() if d >= max_dur * ratio or d >= min_major_sec}
    if len(majors) >= len(durs):
        return diar_segs
    remap = {}
    for i, (s, e, spk) in enumerate(diar_segs):
        if spk in majors:
            continue
        # 找最近的相邻主簇
        prev_major = next((x[2] for x in reversed(diar_segs[:i]) if x[2] in majors), None)
        next_major = next((x[2] for x in diar_segs[i + 1:] if x[2] in majors), None)
        remap[spk] = prev_major if prev_major is not None else (
            next_major if next_major is not None else spk)
    return [(s, e, remap.get(spk, spk)) for s, e, spk in diar_segs]


def assign_speakers(transcript_segments, diar_segments):
    """把 diarization 结果按时间最大重叠归属到转写片段，并按首次出现顺序重排编号。

    输入 [(start, end, text)]，输出 [(start, end, text, speaker_idx)]。
    无重叠时说话人记 -1（未知）。重排后 说话人1 = 最先开口的声纹簇。
    """
    raw = []
    for st, en, text in transcript_segments:
        best, best_overlap = -1, 0.0
        for dst, den, spk in diar_segments:
            overlap = min(en, den) - max(st, dst)
            if overlap > best_overlap:
                best, best_overlap = spk, overlap
        raw.append((st, en, text, best))

    order = {}
    for _st, _en, _text, spk in raw:
        if spk >= 0 and spk not in order:
            order[spk] = len(order)
    out = [(st, en, text, order.get(spk, -1) if spk >= 0 else -1)
           for st, en, text, spk in raw]
    return out

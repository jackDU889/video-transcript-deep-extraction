"""本地语音转写：双引擎。

- mlx-whisper：Apple Silicon GPU（Metal），首选，速度快一个数量级
- faster-whisper：CPU 兜底（ctranslate2），无 GPU/无本地 mlx 权重时使用

模型本地缓存于 模型/ 目录，命中则完全离线，不访问 HuggingFace。
"""
import os
import time

TASK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_MODEL_DIR = os.path.join(TASK_DIR, "模型")


def _local_mlx_dir(name: str) -> str:
    p = os.path.join(LOCAL_MODEL_DIR, name)
    return p if os.path.isfile(os.path.join(p, "weights.safetensors")) else ""


def _local_fw_dir(name: str) -> str:
    p = os.path.join(LOCAL_MODEL_DIR, name)
    if os.path.isfile(os.path.join(p, "model.bin")):
        return p
    p = os.path.join(LOCAL_MODEL_DIR, f"faster-whisper-{name}")
    return p if os.path.isfile(os.path.join(p, "model.bin")) else ""


def transcribe(audio_path: str, engine: str = "auto",
               fw_model: str = "large-v3",
               mlx_model: str = "whisper-large-v3-turbo-mlx"):
    """返回 (segments, media_sec, engine_used)。segments 为 [(start, end, text)]。"""
    if engine == "mlx" or (engine == "auto" and _local_mlx_dir(mlx_model)):
        try:
            return _transcribe_mlx(audio_path, mlx_model)
        except ImportError as e:
            if engine == "mlx":
                raise SystemExit(f"[错误] 未安装 mlx-whisper（{e}），请先 .venv/bin/pip install mlx-whisper")
            print("[ASR] mlx-whisper 不可用，退回 faster-whisper。", flush=True)
    return _transcribe_fw(audio_path, fw_model)


def _transcribe_mlx(audio_path: str, mlx_model: str):
    import mlx_whisper

    ref = _local_mlx_dir(mlx_model) or mlx_model
    print(f"[ASR] 引擎 mlx-whisper（GPU）| 模型: {ref}", flush=True)
    t0 = time.time()
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=ref,
        language="zh",
        initial_prompt="以下是普通话的视频内容，请用简体中文输出。",
        verbose=False,
    )
    out = []
    for seg in result.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if text:
            out.append((float(seg["start"]), float(seg["end"]), text))
    print(f"[ASR] 转写完成：{len(out)} 段，用时 {time.time()-t0:.1f}s", flush=True)
    return out, 0.0, "mlx"


def _transcribe_fw(audio_path: str, fw_model: str):
    from faster_whisper import WhisperModel

    model_ref = _local_fw_dir(fw_model) or fw_model
    print(f"[ASR] 引擎 faster-whisper（CPU）| 模型: {model_ref}", flush=True)
    t0 = time.time()
    model = WhisperModel(model_ref, device="cpu", compute_type="int8")

    from faster_whisper import BatchedInferencePipeline
    try:
        pipe = BatchedInferencePipeline(model=model)  # CPU 上通常快于逐段推理
        segments_iter, info = pipe.transcribe(
            audio_path, language="zh", beam_size=1, batch_size=8,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt="以下是普通话的视频内容，请用简体中文输出。",
        )
    except Exception as e:
        print(f"[ASR] 批处理推理不可用（{e}），退回逐段模式。", flush=True)
        segments_iter, info = model.transcribe(
            audio_path, language="zh", beam_size=5,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt="以下是普通话的视频内容，请用简体中文输出。",
        )

    out = []
    for seg in segments_iter:  # 生成器：边转边收
        text = seg.text.strip()
        if text:
            out.append((seg.start, seg.end, text))
        if len(out) % 30 == 0:
            print(f"[ASR] 进度 {seg.end:.0f}s / {info.duration:.0f}s", flush=True)
    print(f"[ASR] 转写完成：{len(out)} 段，用时 {time.time()-t0:.1f}s", flush=True)
    return out, float(info.duration or 0), "fw"

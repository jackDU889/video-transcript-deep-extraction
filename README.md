# 视频内容文案深度提取

输入视频链接（B站 / 抖音 / 本地文件），输出 **100% 原文**的逐字转录稿（只做段落划分，不改任何字），自动进行说话人分离标注，并可选调用 LLM 生成「深度学习导读」分析文章初稿。

## 功能特性

- **字幕优先**：B站有登录态时优先拉官方/AI字幕（wbi 签名接口）；无登录态或失败自动回退本地 whisper 转写，流程不中断
- **双引擎本地转写**：Apple Silicon 上首选 mlx-whisper（GPU，快约一个数量级），其他环境自动回退 faster-whisper（CPU）；模型全部本地缓存，命中即完全离线
- **说话人分离**：pyannote segmentation ONNX + 3D-Speaker Cam++ 声纹聚类（sherpa-onnx 离线推理），多说话人视频自动标注【说话人N】并按换人切分段落
- **100% 一致性硬校验**：只分组不改字；程序逐字校验「段落拼接 == 原始转写」，不一致拒绝产出
- **完整性保障**：下载后校验媒体时长与平台标注一致（差 >5 秒拒绝产出）；转写后校验时间轴覆盖到片尾，结果写入 `meta.json` 的 `coverage` 字段
- **LLM 分析初稿**：按「深度学习导读」七段框架（一图速览/背景知识/论证拆解/批判视角/实践建议/延伸学习/术语表）调用 OpenAI 兼容接口生成初稿
- **批量与队列**：支持 UP 主全空间爬取、CSV 批量任务表、采集/分析双队列并发，幂等可重跑

## 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | macOS（Apple Silicon 推荐，可启用 GPU 加速）；Linux/Windows 走 CPU 引擎也可运行 |
| Python | 3.10+（开发环境为 3.13） |
| 系统工具 | ffmpeg、yt-dlp、aria2 |
| 磁盘 | 约 8 GB（模型 4.5 GB + 虚拟环境 1.5 GB + 产出空间） |
| 网络 | 模型下载一次性完成；HuggingFace 直连不可用时走 hf-mirror 镜像 |

## 快速开始

### 1. 克隆并安装依赖

```bash
git clone <本仓库地址>
cd 视频内容文案深度提取

# 系统工具（macOS）
brew install yt-dlp ffmpeg aria2

# Python 虚拟环境
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> 非 Apple Silicon 平台：编辑 `requirements.txt`，注释掉 `mlx-whisper` 和 `mlx` 两行，程序会自动使用 faster-whisper（CPU）。

### 2. 下载模型（一次性，约 4.5 GB）

所有模型放在项目根目录的 `模型/` 下，目录结构必须如下：

```
模型/
├── faster-whisper-large-v3/            # faster-whisper（CPU 引擎）
│   ├── model.bin
│   ├── config.json
│   ├── tokenizer.json
│   ├── preprocessor_config.json
│   └── vocabulary.json
├── whisper-large-v3-turbo-mlx/         # mlx-whisper（GPU 引擎，仅 Apple Silicon）
│   ├── weights.safetensors
│   └── config.json
└── speaker-diarization/                # 说话人分离
    ├── sherpa-onnx-pyannote-segmentation-3-0/
    │   └── model.onnx
    └── campplus-zh.onnx
```

下载命令（已逐一验证可用）：

```bash
mkdir -p 模型/speaker-diarization

# 1) faster-whisper-large-v3（约 3.1 GB，HF: Systran/faster-whisper-large-v3）
#    国内网络加 HF_ENDPOINT=https://hf-mirror.com 走镜像
HF_ENDPOINT=https://hf-mirror.com .venv/bin/huggingface-cli download \
  Systran/faster-whisper-large-v3 --local-dir 模型/faster-whisper-large-v3

# 2) whisper-large-v3-turbo MLX 权重（约 1.6 GB，HF: mlx-community/whisper-large-v3-turbo）
HF_ENDPOINT=https://hf-mirror.com .venv/bin/huggingface-cli download \
  mlx-community/whisper-large-v3-turbo --local-dir 模型/whisper-large-v3-turbo-mlx

# 3a) pyannote 说话人分割模型（sherpa-onnx 转换版，解压出 sherpa-onnx-pyannote-segmentation-3-0/ 目录）
curl -L -o /tmp/pyannote-seg.tar.bz2 \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
tar -xjf /tmp/pyannote-seg.tar.bz2 -C 模型/speaker-diarization

# 3b) Cam++ 中文声纹模型（下载后即为 campplus-zh.onnx，官方 release 原名 3dspeaker_...）
curl -L -o 模型/speaker-diarization/campplus-zh.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx
```

> 下载大模型也可用 `aria2c -x8`（brew 已装 aria2）加速：先从 hf-mirror 页面复制直链再 aria2 下载，放入对应目录即可。模型目录命中后程序完全离线运行，不访问 HuggingFace。

### 3. 配置凭据（可选，按需）

#### B站登录态（可选，推荐）

提供 SESSDATA 后可优先拉 B站官方/AI字幕（比转写快得多、质量高），同时降低下载被 412 风控的概率。三选一：

```bash
# 方式一：写入主目录文件（推荐，权限 600）
echo '你的SESSDATA值' > ~/.bilibili_sessdata && chmod 600 ~/.bilibili_sessdata

# 方式二：环境变量
export BILIBILI_SESSDATA='你的SESSDATA值'

# 方式三：命令行参数（临时）
.venv/bin/python extract.py <链接> --sessdata '你的SESSDATA值'

# 方式四：直接读浏览器登录态
.venv/bin/python extract.py <链接> --cookies-from-browser edge   # chrome/edge/safari 等
```

> SESSDATA 获取：登录 bilibili.com 后，浏览器 DevTools → Application → Cookies → 复制 `SESSDATA` 的值。凭据只存放在主目录，不写入项目、不进入任何产出文件。

#### LLM API（仅 `analyze.py` 需要）

`analyze.py` 调用 OpenAI 兼容接口，凭据读取自 `~/.zcode/v2/config.json`（不落盘到项目目录）。若无该文件，新建一个：

```json
{
  "provider": {
    "my-llm": {
      "kind": "openai-compatible",
      "name": "my-llm",
      "models": { "glm-5.3-flash": {} },
      "options": {
        "baseURL": "https://<你的OpenAI兼容接口地址>/v1",
        "apiKey": "<你的API Key>"
      }
    }
  }
}
```

说明：默认调用模型 `glm-5.3-flash`；程序会在 `provider` 下查找 `kind` 为 `openai-compatible`、且 `models` 中含该模型名（或条目名含该模型名）的供应商。要换模型，把 `models` 的键改成你的供应商支持的模型 ID 即可。

### 4. 运行

```bash
# 1) 提取原始文案（100% 逐字转录）
.venv/bin/python extract.py <B站/抖音链接或本地音视频文件路径> [选项]

# 2) 生成深度学习导读（LLM 初稿）
.venv/bin/python analyze.py "输出/<任务目录>"
```

`extract.py` 常用选项：

| 选项 | 说明 |
|---|---|
| `--engine auto\|mlx\|fw` | 转写引擎（auto=本地 mlx 权重优先，默认） |
| `--model large-v3` | faster-whisper 模型名（fw 引擎用） |
| `--no-ts` | 段首不加 `[mm:ss]` 导航时间戳 |
| `--sessdata XXX` | B站登录态（见上文三种传入方式） |
| `--cookies-from-browser edge` | 从浏览器读登录态（chrome/edge/safari 等） |
| `--diarize auto\|on\|off` | 说话人分离（auto=模型可用即自动，默认） |
| `--num-speakers N` | 强制说话人数量（0=自动估计；已知人数时最稳） |
| `--diarize-threshold 0.6` | 声纹聚类阈值（说话人声音相近时调低如 0.45） |

> `analyze.py` 同目录已存在 `02_深度学习导读.md` 时不覆盖，改写入 `02_深度学习导读_API初稿.md`。

## 输出结构

每次提取在 `输出/` 下生成一个任务目录：

```
输出/20260906_<视频标题>_<UP主>/
├── 01_原始文案.md           # 100% 逐字转录，段首带 [mm:ss] 导航时间戳，多说话人带【说话人N】标注
├── 02_深度学习导读.md       # analyze.py 生成的 LLM 分析初稿
├── meta.json                # 元数据：标题/UP主/时长/转写方式/覆盖率/series 等
└── 原始素材/
    ├── media.m4a            # 下载的完整音轨
    └── raw_segments.json    # 原始转写片段（带时间戳，备查）
```

## 技术架构

```
extract.py ──> lib/platforms.py   平台识别（B站/抖音/视频号/本地文件）
           ──> lib/fetch.py       元数据、短链解析、媒体下载（yt-dlp，412 时自动改用 playurl API）
           ──> lib/subtitles.py   B站官方/AI字幕（wbi 签名，需 SESSDATA）
           ──> lib/asr.py         双引擎转写：mlx-whisper(GPU首选) / faster-whisper(CPU兜底)
           ──> lib/diarize.py     说话人分离（pyannote分割ONNX + Cam++声纹聚类，sherpa-onnx）
           ──> lib/segment.py     段落划分（换人强制分段）+ 100%逐字一致性硬校验
analyze.py ──> lib/llm.py         OpenAI兼容 LLM 调用（凭据读自 ~/.zcode/v2/config.json）
```

## 平台支持现状

| 平台 | 状态 | 说明 |
|---|---|---|
| B站 | ✅ 已实测 | 全链路跑通；AI字幕通道已实测（单视频 473 条字幕）；风控自动应对 |
| 抖音 | 已实现，待实测 | 支持解析 v.douyin.com 短链，yt-dlp 下载；部分链接可能需要 Cookie |
| 微信视频号 | 本地文件兜底 | 网页端需扫码登录，暂无稳定免登录通道；导出视频文件直接丢路径即可 |
| 本地文件 | ✅ 支持 | mp4/mov/m4a/mp3 等常见音视频格式 |

## 批量与队列（全空间 crawling）

```bash
# 1) 爬取 UP 主空间视频清单与系列归属（幂等，可重复执行）→ 视频清单.json
.venv/bin/python crawl_space.py

# 2) 两条队列并行（各自后台跑，互不等待）：
.venv/bin/python extract_queue.py > 队列日志_采集.log 2>&1   # 采集队列：字幕优先/whisper兜底 → 01
.venv/bin/python analyze_queue.py > 队列日志_分析.log 2>&1   # 分析队列：轮询新产出 → 02

# 3) 单表批量（CSV：每行【标题】链接，自动回写状态）：
.venv/bin/python batch.py 批量任务.csv
```

- 双队列完全解耦并发；分析队列每个目录最多重试 2 次，失败写 `02_分析失败.txt`；采集完成写 `采集完成.flag`，分析队列随后自行退出
- 幂等性：采集队列按 meta.json 中的 bvid 跳过已完成任务，重跑无副作用
- 风控应对：yt-dlp 被 B站 412 时自动改用 playurl API 直连下载音频；队列级退避 45s 重试 + 冷却 90s

## 说话人分离说明

- 输出匿名标签【说话人1/2/…】（按首次出现顺序），标签是导航性标注，不混入正文，不影响 100% 校验
- 碎簇自动合并（短插话/笑声导致的过度分裂）；单人视频不标注
- 局限：重叠说话与极短插话边界存在固有限制；TTS 合成音（同引擎多音色）可能被误判为单说话人

## 常见问题

| 现象 | 处理 |
|---|---|
| 提示未找到模型 / 回退 CPU 很慢 | 确认 `模型/` 目录结构与上文一致；`--engine fw` 走 CPU 属正常速度（约 GPU 的 1/10） |
| B站下载 412 | 配置 SESSDATA（登录态会注入下载请求）；队列模式会自动退避重试 |
| HuggingFace 下载超时 | 加 `HF_ENDPOINT=https://hf-mirror.com`，或用 aria2 从镜像页复制直链下载 |
| `analyze.py` 报找不到供应商 | 检查 `~/.zcode/v2/config.json` 的 `provider` 结构是否符合上文示例 |
| 同音字噪声（如"绘画信息"=会话信息） | 属 whisper 特性，**原文一字不改**是本项目铁律；建议在分析文章末尾附同音字对照表 |

## 安全与隐私

- 所有凭据（SESSDATA、LLM API Key）只存放在用户主目录，代码仓库内无任何硬编码密钥
- `模型/`、`输出/`、`测试/`、`.venv/`、运行日志均已列入 `.gitignore`，不会进入版本库

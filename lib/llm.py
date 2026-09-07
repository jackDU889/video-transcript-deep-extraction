"""LLM API 接入：OpenAI 兼容 + Anthropic 双协议，供应商优先级链与自动回退。

优先级（时间感知）：
- 2026-09-07 09:00 前：BigModel 体验额度 GLM-5.3-Flash 优先，失败自动回退 RJ-glm-5.3-flash
- 之后：默认 RJ-glm-5.3-flash
凭据读取自 ~/.zcode/v2/config.json（ZCode 供应商配置），不落盘到工作区。
"""
import datetime
import json
import os

import requests

CONFIG_PATH = os.path.expanduser("~/.zcode/v2/config.json")
DEFAULT_MODEL = "glm-5.3-flash"

# BigModel 体验额度优先期（用户指定：到 2026-09-07 09:00 前），过后自动回退默认供应商
# 注：ZCode「Weekend Build 体验套餐」(builtin:bigmodel-start-plan) 为客户端专用端点，
#     有 captcha 指纹校验，外部脚本无法直调（实测400/401）；该额度由 ZCode 会话内
#     撰写分析文章时消耗。程序侧优先走 coding-plan，失败回退 RJ。
BIGMODEL_PREFER_UNTIL = datetime.datetime(2026, 9, 7, 9, 0, 0)


def _load_providers() -> dict:
    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    return cfg.get("provider") or cfg.get("providers") or {}


def _find_openai(model: str):
    """OpenAI 兼容供应商（RJ-*）：按 wire 模型ID或条目名匹配。"""
    model = (model or DEFAULT_MODEL).strip()
    for pid, p in _load_providers().items():
        if (p.get("kind") or "").lower() != "openai-compatible":
            continue
        opts = p.get("options") or {}
        base, key = opts.get("baseURL") or opts.get("baseUrl") or "", opts.get("apiKey") or ""
        if not base or not key:
            continue
        ids = list((p.get("models") or {}).keys())
        if model in ids or model in (p.get("name") or ""):
            wire = model if model in ids else (ids[0] if ids else model)
            return {"name": p.get("name") or pid, "protocol": "openai",
                    "baseURL": base.rstrip("/"), "apiKey": key, "model": wire}
    return None


def _find_bigmodel(model_display: str = "GLM-5.3-Flash"):
    """BigModel 体验额度（Anthropic 格式）：取配置里带有效 key 的 bigmodel plan。"""
    for pid, p in _load_providers().items():
        name = (p.get("name") or "").lower()
        pid_l = pid.lower()
        if "bigmodel" not in name and "bigmodel" not in pid_l:
            continue
        if (p.get("kind") or "").lower() != "anthropic":
            continue
        if model_display not in (p.get("models") or {}):
            continue
        opts = p.get("options") or {}
        base, key = opts.get("baseURL") or "", opts.get("apiKey") or ""
        if not base or not key:
            continue
        return {"name": p.get("name") or pid, "protocol": "anthropic",
                "baseURL": base.rstrip("/"), "apiKey": key, "model": model_display}
    return None


def resolve_chain(model: str = "") -> list:
    """按优先级返回候选供应商：优先期内 BigModel 在队首，失败自动回退默认。"""
    chain = []
    if datetime.datetime.now() < BIGMODEL_PREFER_UNTIL:
        bm = _find_bigmodel()
        if bm:
            chain.append(bm)
    oai = _find_openai(model or DEFAULT_MODEL)
    if oai:
        chain.append(oai)
    if not chain:
        raise SystemExit(f"[错误] 未在 {CONFIG_PATH} 找到可用供应商（模型 {model}）")
    return chain


def _chat_openai(prov: dict, system: str, user: str, temperature: float,
                 max_tokens: int, timeout: int) -> str:
    resp = requests.post(
        f"{prov['baseURL']}/chat/completions",
        headers={"Authorization": f"Bearer {prov['apiKey']}", "Content-Type": "application/json"},
        json={"model": prov["model"],
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "temperature": temperature, "max_tokens": max_tokens},
        timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(f"空内容: {json.dumps(data, ensure_ascii=False)[:300]}")
    return content


def _chat_anthropic(prov: dict, system: str, user: str, temperature: float,
                    max_tokens: int, timeout: int) -> str:
    resp = requests.post(
        f"{prov['baseURL']}/v1/messages",
        headers={"x-api-key": prov["apiKey"], "Authorization": f"Bearer {prov['apiKey']}",
                 "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": prov["model"], "max_tokens": max_tokens, "temperature": temperature,
              "system": system, "messages": [{"role": "user", "content": user}]},
        timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    content = "".join(b.get("text", "") for b in data.get("content", []))
    if not content:
        raise RuntimeError(f"空内容: {json.dumps(data, ensure_ascii=False)[:300]}")
    return content


def chat_auto(system: str, user: str, model: str = "", temperature: float = 0.4,
              max_tokens: int = 16000, timeout: int = 600):
    """按优先级链尝试各供应商，返回 (text, provider)。全部失败抛异常。"""
    errs = []
    for prov in resolve_chain(model):
        try:
            fn = _chat_anthropic if prov["protocol"] == "anthropic" else _chat_openai
            text = fn(prov, system, user, temperature, max_tokens, timeout)
            return text, prov
        except Exception as e:
            errs.append(f"{prov['name']}({prov['model']}): {e}")
            print(f"[LLM] {prov['name']} ({prov['model']}) 失败：{e}\n[LLM] 尝试下一个供应商…")
    raise RuntimeError("所有供应商均失败：" + " | ".join(errs))


def resolve_provider(model: str = "") -> dict:
    """兼容旧接口：返回链上第一个供应商。"""
    return resolve_chain(model)[0]

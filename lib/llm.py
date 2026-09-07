"""LLM API 接入：OpenAI 兼容协议，自动查找可用供应商。

凭据读取自 ~/.zcode/v2/config.json（结构见 README「LLM API」一节），
不落盘到项目目录。
"""
import json
import os

import requests

CONFIG_PATH = os.path.expanduser("~/.zcode/v2/config.json")
DEFAULT_MODEL = "glm-5.3-flash"


def _load_providers() -> dict:
    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    return cfg.get("provider") or cfg.get("providers") or {}


def _find_openai(model: str):
    """OpenAI 兼容供应商：按 wire 模型ID或条目名匹配。"""
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


def resolve_chain(model: str = "") -> list:
    """返回可用供应商链（当前为单供应商，保留列表结构以便扩展回退链）。"""
    chain = []
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


def chat_auto(system: str, user: str, model: str = "", temperature: float = 0.4,
              max_tokens: int = 16000, timeout: int = 600):
    """按供应商链尝试，返回 (text, provider)。全部失败抛异常。"""
    errs = []
    for prov in resolve_chain(model):
        try:
            text = _chat_openai(prov, system, user, temperature, max_tokens, timeout)
            return text, prov
        except Exception as e:
            errs.append(f"{prov['name']}({prov['model']}): {e}")
            print(f"[LLM] {prov['name']} ({prov['model']}) 失败：{e}\n[LLM] 尝试下一个供应商…")
    raise RuntimeError("所有供应商均失败：" + " | ".join(errs))


def resolve_provider(model: str = "") -> dict:
    """兼容旧接口：返回链上第一个供应商。"""
    return resolve_chain(model)[0]

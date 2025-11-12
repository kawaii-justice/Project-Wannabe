import httpx
from typing import Optional

from .settings import load_settings, DEFAULT_SETTINGS


class ContextUtilsError(Exception):
    pass


async def get_true_max_context_length(base_url: str) -> Optional[int]:
    """
    KoboldCppの /api/extra/true_max_context_length から
    実際のmax context lengthを毎回取得する。

    失敗した場合は None を返す（呼び出し側でフォールバック扱い）。
    """
    url = f"{base_url}/api/extra/true_max_context_length"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            value = data.get("value")
            if isinstance(value, int) and value > 0:
                return value
            return None
        except Exception as e:
            print(f"[ContextUtils] Failed to get true_max_context_length: {e}")
            return None


async def count_tokens(base_url: str, text: str) -> Optional[int]:
    """
    KoboldCppの /api/extra/tokencount を使ってテキストのトークン数を取得する。

    成功時はトークン数(int)、失敗時はNone。
    """
    url = f"{base_url}/api/extra/tokencount"
    payload = {"prompt": text}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            value = data.get("value")
            if isinstance(value, int) and value >= 0:
                return value
            return None
        except Exception as e:
            print(f"[ContextUtils] Failed to count tokens: {e}")
            return None


def get_available_context(true_ctx: Optional[int], max_output_tokens: int) -> Optional[int]:
    """
    利用可能なプロンプトトークン数 (true_ctx - max_output_tokens) を計算する。
    true_ctxまたはmax_output_tokensが不正な場合はNone。
    """
    if true_ctx is None:
        return None
    if max_output_tokens is None or max_output_tokens <= 0:
        return None
    available = true_ctx - max_output_tokens
    return available if available > 0 else None


# true_max_context_length取得失敗時のフォールバック値
# （Project側での内部安全マージンとしてのみ使用。設定値とは紐付けない）
def get_fallback_max_context_length() -> int:
    return int(DEFAULT_SETTINGS.get("max_main_text_chars", 8192))

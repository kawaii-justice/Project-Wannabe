import httpx
import json
import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator, Dict, Any, Optional, List

from src.core.settings import load_settings, DEFAULT_SETTINGS


class KoboldClientError(Exception):
    """Custom exception for KoboldClient errors."""
    pass


@dataclass
class ChatStreamEvent:
    content: str = ""
    reasoning_content: str = ""


class KoboldClient:
    """
    Asynchronous client for interacting with the KoboldCpp API,
    specifically for streaming generation.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=None)
        self._current_settings = load_settings()

    def _get_api_base_url(self) -> str:
        port = self._current_settings.get("kobold_port", 5001)
        return f"http://127.0.0.1:{port}"

    def _get_generate_stream_url(self) -> str:
        return f"{self._get_api_base_url()}/api/extra/generate/stream"

    def _get_chat_completions_stream_url(self) -> str:
        return f"{self._get_api_base_url()}/lcpp/v1/chat/completions"

    def reload_settings(self):
        self._current_settings = load_settings()
        print("KoboldClient settings reloaded.")

    async def _open_streaming_response(self, api_url: str, payload: Dict[str, Any]) -> httpx.Response:
        request = self.client.build_request("POST", api_url, json=payload)
        return await self.client.send(request, stream=True)

    async def generate_stream(
        self,
        prompt: str,
        max_length: Optional[int] = None,
        generation_params: Optional[Dict[str, Any]] = None,
        stop_sequence: Optional[List[str]] = None,
        banned_tokens: Optional[List[int]] = None,
        banned_strings: Optional[List[str]] = None,
        current_mode: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        api_url = self._get_generate_stream_url()
        params_to_send = {
            "temperature": self._current_settings.get("temperature"),
            "min_p": self._current_settings.get("min_p"),
            "top_p": self._current_settings.get("top_p"),
            "top_k": self._current_settings.get("top_k"),
            "rep_pen": self._current_settings.get("rep_pen"),
            "stop_sequence": stop_sequence if stop_sequence is not None else self._current_settings.get("stop_sequences", []),
            "banned_tokens": self._current_settings.get("banned_tokens", []),
        }
        if generation_params:
            gen_params_copy = generation_params.copy()
            gen_params_copy.pop("stop_sequence", None)
            params_to_send.update(gen_params_copy)

        if params_to_send.get("top_k") == 0:
            del params_to_send["top_k"]

        payload = {
            "prompt": prompt,
            **params_to_send,
        }

        if current_mode == "generate":
            payload["ban_eos_token"] = True

        if banned_strings is not None:
            payload["banned_tokens"] = banned_strings

        if banned_tokens is not None:
            if "logit_bias" not in payload:
                payload["logit_bias"] = {}
            for token in banned_tokens:
                payload["logit_bias"][str(token)] = -1000

        if max_length is not None:
            payload["max_length"] = max_length

        payload = {k: v for k, v in payload.items() if v is not None}

        print(f"Sending request to {api_url} with payload: {json.dumps(payload, indent=2)}")

        response: Optional[httpx.Response] = None
        line_iter = None
        try:
            response = await self._open_streaming_response(api_url, payload)
            if response.status_code != 200:
                error_content = await response.aread()
                raise KoboldClientError(
                    f"API Error: Status {response.status_code} - {error_content.decode()}"
                )

            line_iter = response.aiter_lines()
            while True:
                try:
                    line = await anext(line_iter)
                except StopAsyncIteration:
                    break
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    print("Stream finished ([DONE] received).")
                    break
                try:
                    data = json.loads(data_str)
                    token = data.get("token")
                    if token:
                        yield token
                    elif "error" in data:
                        print(f"Error in stream data: {data['error']}")
                except json.JSONDecodeError:
                    print(f"Warning: Could not decode JSON data: {data_str}")
                except Exception as e:
                    print(f"Error processing stream line: {line}, Error: {e}")
        except httpx.ConnectError as e:
            raise KoboldClientError(f"Connection Error: Could not connect to {api_url}. Is KoboldCpp running? Details: {e}")
        except httpx.TimeoutException as e:
            raise KoboldClientError(f"Timeout Error: Request to {api_url} timed out. Details: {e}")
        except httpx.RequestError as e:
            raise KoboldClientError(f"Request Error: An error occurred during the request to {api_url}. Details: {e}")
        except Exception as e:
            raise KoboldClientError(f"An unexpected error occurred during streaming: {e}")
        finally:
            if line_iter is not None and hasattr(line_iter, "aclose"):
                try:
                    await line_iter.aclose()
                except RuntimeError as e:
                    if "cancel scope" not in str(e).lower():
                        pass
                except Exception:
                    pass
            if response is not None:
                try:
                    await response.aclose()
                except RuntimeError as e:
                    if "cancel scope" not in str(e).lower():
                        pass
                except Exception:
                    pass

    async def generate_chat_stream(
        self,
        messages: List[Dict[str, str]],
        assistant_prefill: Optional[str] = None,
        max_length: Optional[int] = None,
        generation_params: Optional[Dict[str, Any]] = None,
        stop_sequence: Optional[List[str]] = None,
        current_mode: Optional[str] = None,
    ) -> AsyncGenerator[ChatStreamEvent, None]:
        api_url = self._get_chat_completions_stream_url()

        effective_messages = [dict(message) for message in messages]
        if assistant_prefill:
            if effective_messages and effective_messages[-1].get("role") == "assistant":
                effective_messages[-1]["content"] = assistant_prefill
            else:
                effective_messages.append({"role": "assistant", "content": assistant_prefill})

        payload: Dict[str, Any] = {
            "messages": effective_messages,
            "stream": True,
            "temperature": self._current_settings.get("temperature"),
            "min_p": self._current_settings.get("min_p"),
            "top_p": self._current_settings.get("top_p"),
            "top_k": self._current_settings.get("top_k"),
            "rep_pen": self._current_settings.get("rep_pen"),
            "banned_tokens": self._current_settings.get("banned_tokens", []),
        }

        if max_length is not None:
            payload["max_tokens"] = max_length
        if stop_sequence:
            payload["stop"] = stop_sequence
        if generation_params:
            payload.update(generation_params)

        chat_template_kwargs = payload.get("chat_template_kwargs")
        if chat_template_kwargs is not None and not chat_template_kwargs:
            payload.pop("chat_template_kwargs", None)

        if payload.get("top_k") == 0:
            payload.pop("top_k", None)

        if current_mode == "generate":
            payload["ban_eos_token"] = True

        payload = {k: v for k, v in payload.items() if v is not None}
        print(f"Sending chat request to {api_url} with payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")

        response: Optional[httpx.Response] = None
        line_iter = None
        try:
            response = await self._open_streaming_response(api_url, payload)
            if response.status_code != 200:
                error_content = await response.aread()
                decoded = error_content.decode(errors="replace")
                if response.status_code == 404:
                    raise KoboldClientError(
                        "API Error: /lcpp/v1/chat/completions が見つかりません。KoboldCpp の更新版を使っているか確認してください。"
                    )
                if "template" in decoded.lower() or "jinja" in decoded.lower():
                    raise KoboldClientError(
                        f"API Error: Chat template 適用に失敗しました。KoboldCpp を --jinja 付きで起動しているか確認してください。Details: {decoded}"
                    )
                raise KoboldClientError(
                    f"API Error: Status {response.status_code} - {decoded}"
                )

            line_iter = response.aiter_lines()
            while True:
                try:
                    line = await anext(line_iter)
                except StopAsyncIteration:
                    break
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    print("Chat stream finished ([DONE] received).")
                    break
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content") or ""
                    reasoning_content = delta.get("reasoning_content") or ""
                    if content or reasoning_content:
                        yield ChatStreamEvent(
                            content=content,
                            reasoning_content=reasoning_content,
                        )
                except json.JSONDecodeError:
                    print(f"Warning: Could not decode JSON data: {data_str}")
                except Exception as e:
                    print(f"Error processing chat stream line: {line}, Error: {e}")
        except httpx.ConnectError as e:
            raise KoboldClientError(f"Connection Error: Could not connect to {api_url}. Is KoboldCpp running? Details: {e}")
        except httpx.TimeoutException as e:
            raise KoboldClientError(f"Timeout Error: Request to {api_url} timed out. Details: {e}")
        except httpx.RequestError as e:
            raise KoboldClientError(f"Request Error: An error occurred during the request to {api_url}. Details: {e}")
        except Exception as e:
            raise KoboldClientError(f"An unexpected error occurred during chat streaming: {e}")
        finally:
            if line_iter is not None and hasattr(line_iter, "aclose"):
                try:
                    await line_iter.aclose()
                except RuntimeError as e:
                    if "cancel scope" not in str(e).lower():
                        pass
                except Exception:
                    pass
            if response is not None:
                try:
                    await response.aclose()
                except RuntimeError as e:
                    if "cancel scope" not in str(e).lower():
                        pass
                except Exception:
                    pass

    async def close(self):
        await self.client.aclose()


async def main():
    client = KoboldClient()
    client.reload_settings()

    test_prompt = "<s>[INST] Write a short story about a brave knight. [/INST]"
    print(f"\n--- Testing generate_stream with prompt: ---\n{test_prompt}\n------------------------------------------")

    try:
        full_response = ""
        async for token in client.generate_stream(test_prompt):
            print(token, end="", flush=True)
            full_response += token
        print("\n--- Stream finished ---")

    except KoboldClientError as e:
        print(f"\n--- Error during generation: {e} ---")
    finally:
        await client.close()
        print("\nClient closed.")


if __name__ == "__main__":
    load_settings()
    asyncio.run(main())

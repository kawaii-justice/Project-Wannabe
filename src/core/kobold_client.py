import httpx
import json
import asyncio
from typing import AsyncGenerator, Dict, Any, Optional, List

from src.core.settings import load_settings, DEFAULT_SETTINGS
from src.core.prompt_builder import build_prompt_with_compression

class KoboldClientError(Exception):
    """Custom exception for KoboldClient errors."""
    pass

class KoboldClient:
    """
    Asynchronous client for interacting with the KoboldCpp API,
    specifically for streaming generation.
    """
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=None) # Allow long-running streams
        self._current_settings = load_settings() # Load initial settings

    def _get_api_base_url(self) -> str:
        """Base URL (host+port) for KoboldCpp."""
        port = self._current_settings.get("kobold_port", 5001)
        return f"http://127.0.0.1:{port}"

    def _get_generate_stream_url(self) -> str:
        """Constructs the streaming generation API URL."""
        return f"{self._get_api_base_url()}/api/extra/generate/stream"

    def reload_settings(self):
        """Reloads settings from the config file."""
        self._current_settings = load_settings()
        print("KoboldClient settings reloaded.") # For debugging

    async def generate_stream(
        self,
        prompt: str,
        max_length: Optional[int] = None, # Add max_length parameter
        generation_params: Optional[Dict[str, Any]] = None,
        stop_sequence: Optional[List[str]] = None, # Add stop_sequence parameter
        banned_tokens: Optional[List[int]] = None, # Add banned_tokens parameter
        current_mode: Optional[str] = None # Add current_mode parameter to know if it's generate or idea
    ) -> AsyncGenerator[str, None]:
        """
        Sends a prompt to the KoboldCpp streaming API and yields generated tokens.

        重要:
        - 呼び出し元で build_prompt_with_compression(...) により
          最大コンテキスト長 - 最大出力長の制約内に収まるプロンプトを構築してから渡す想定。
        - この関数自身は渡されたpromptをそのまま送信する（追加圧縮は行わない）。

        Args:
            prompt: The input prompt string.
            max_length: Optional specific max_length for this generation request.
            generation_params: Optional dictionary overriding other default generation parameters.
            stop_sequence: Optional list of strings to use as stop sequences, overriding settings.

        Yields:
            str: Generated text chunks (tokens).

        Raises:
            KoboldClientError: If connection fails or API returns an error status.
        """
        api_url = self._get_generate_stream_url()
        # Combine default settings with overrides, prioritizing the max_length argument
        params_to_send = {
            # "max_length": self._current_settings.get("max_length"), # Removed reading from settings
            "temperature": self._current_settings.get("temperature"),
            "min_p": self._current_settings.get("min_p"),
            "top_p": self._current_settings.get("top_p"),
            "top_k": self._current_settings.get("top_k"), # Add top_k from settings
            "rep_pen": self._current_settings.get("rep_pen"),
            # Stop sequence handling: prioritize argument over settings
            "stop_sequence": stop_sequence if stop_sequence is not None else self._current_settings.get("stop_sequences", []),
        }
        if generation_params:
            # Ensure generation_params doesn't overwrite the prioritized stop_sequence
            gen_params_copy = generation_params.copy()
            gen_params_copy.pop("stop_sequence", None) # Remove stop_sequence if present in overrides
            params_to_send.update(gen_params_copy) # Apply other overrides

        # If top_k is 0 (disabled), remove it from the parameters to send
        if params_to_send.get("top_k") == 0:
            del params_to_send["top_k"]

        payload = {
            "prompt": prompt,
            **params_to_send # Unpack base parameters into payload
        }
        
        # For GEN tasks (generate mode), set ban_eos_token to True
        if current_mode == "generate":
            payload["ban_eos_token"] = True
        
        # Handle custom banned_tokens if provided (using logit_bias)
        if banned_tokens is not None:
            # Initialize logit_bias if not present
            if "logit_bias" not in payload:
                payload["logit_bias"] = {}
            # Add each token with -1000 bias
            for token in banned_tokens:
                payload["logit_bias"][str(token)] = -1000
            
        # Set max_length based on the argument if provided
        if max_length is not None:
            payload["max_length"] = max_length
        # Else, KoboldCpp might use its own default if not provided
        
        # Filter out None values if KoboldCpp doesn't like them
        payload = {k: v for k, v in payload.items() if v is not None}

        print(f"Sending request to {api_url} with payload: {json.dumps(payload, indent=2)}") # Debug log

        try:
            async with self.client.stream("POST", api_url, json=payload) as response:
                # Check for non-200 status codes which indicate an immediate error
                if response.status_code != 200:
                    error_content = await response.aread()
                    raise KoboldClientError(
                        f"API Error: Status {response.status_code} - {error_content.decode()}"
                    )

                # Process the SSE stream
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]": # Check for KoboldCpp specific end signal if any
                            print("Stream finished ([DONE] received).")
                            break
                        try:
                            data = json.loads(data_str)
                            token = data.get("token")
                            if token:
                                yield token
                            # Handle potential errors within the stream if KoboldCpp sends them
                            elif "error" in data:
                                print(f"Error in stream data: {data['error']}")
                                # Decide whether to raise or just log
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
            # Catch unexpected errors during streaming
            raise KoboldClientError(f"An unexpected error occurred during streaming: {e}")

    async def close(self):
        """Closes the underlying HTTP client."""
        await self.client.aclose()


# Example Usage (for testing)
async def main():
    client = KoboldClient()
    # Ensure settings are loaded (e.g., after dialog update)
    client.reload_settings()

    # Example prompt (replace with actual logic later)
    test_prompt = "<s>[INST] Write a short story about a brave knight. [/INST]"
    print(f"\n--- Testing generate_stream with prompt: ---\n{test_prompt}\n------------------------------------------")

    try:
        full_response = ""
        async for token in client.generate_stream(test_prompt):
            print(token, end="", flush=True) # Print tokens as they arrive
            full_response += token
        print("\n--- Stream finished ---")
        # print(f"Full response received:\n{full_response}")

    except KoboldClientError as e:
        print(f"\n--- Error during generation: {e} ---")
    finally:
        await client.close()
        print("\nClient closed.")

if __name__ == "__main__":
    # Load settings initially to create config.json if it doesn't exist
    load_settings()
    asyncio.run(main())

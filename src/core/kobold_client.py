import httpx
import json
import asyncio
from typing import AsyncGenerator, Dict, Any, Optional, List

from src.core.settings import load_settings

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

    def _get_api_url(self) -> str:
        """Constructs the API URL from settings."""
        port = self._current_settings.get("kobold_port", 5001)
        return f"http://127.0.0.1:{port}/api/extra/generate/stream"

    def reload_settings(self):
        """Reloads settings from the config file."""
        self._current_settings = load_settings()
        print("KoboldClient settings reloaded.") # For debugging

    async def generate_stream(
        self,
        prompt: str,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[str, None]:
        """
        Sends a prompt to the KoboldCpp streaming API and yields generated tokens.

        Args:
            prompt: The input prompt string.
            generation_params: Optional dictionary overriding default generation parameters.

        Yields:
            str: Generated text chunks (tokens).

        Raises:
            KoboldClientError: If connection fails or API returns an error status.
        """
        api_url = self._get_api_url()
        # Combine default settings with overrides
        params_to_send = {
            "max_length": self._current_settings.get("max_length"),
            "temperature": self._current_settings.get("temperature"),
            "min_p": self._current_settings.get("min_p"),
            "top_p": self._current_settings.get("top_p"),
            "rep_pen": self._current_settings.get("rep_pen"),
            "stop_sequence": self._current_settings.get("stop_sequences", []),
        }
        if generation_params:
            params_to_send.update(generation_params) # Apply overrides

        payload = {
            "prompt": prompt,
            **params_to_send # Unpack parameters into payload
        }
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

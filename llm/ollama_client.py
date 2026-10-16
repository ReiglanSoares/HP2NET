import json
import queue
import threading
import time
from typing import Callable, Optional

import requests


class GenerationCancelled(Exception):


    def __init__(self, partial_text: str = ""):
        super().__init__("Geração cancelada pelo usuário.")
        self.partial_text = partial_text


class OllamaStreamError(RuntimeError):
    pass
<<<<<<< HEAD:llm_client.py
   
=======
>>>>>>> 794c282 (Refactor phylogenetic agents into modular structure):llm/ollama_client.py

def generate_stream(
    endpoint: str,
    payload: dict,
    label: str = "LLM",
    read_timeout: float = 300,
    cancel_event: Optional[threading.Event] = None,
    on_fragment: Optional[Callable[[str], None]] = None,
    on_metrics: Optional[Callable[[dict], None]] = None,
    progress_interval: float = 0.5,
    show_progress: bool = True,
) -> str:
    """Gera texto pelo Ollama usando streaming e cancelamento."""

    if read_timeout <= 0:
        raise ValueError("read_timeout deve ser positivo.")

    if progress_interval <= 0:
        raise ValueError("progress_interval deve ser positivo.")

    if not isinstance(payload, dict):
        raise TypeError("payload deve ser um dicionário.")

    request_payload = dict(payload)
    request_payload["stream"] = True

    fragments = []
    fragment_count = 0

    started_at = time.monotonic()
    first_fragment_at = None
    last_progress_at = started_at

    completed = False
    metrics = {}

    event_queue = queue.Queue(maxsize=256)

    stop_event = threading.Event()

    external_cancel = cancel_event

    response_lock = threading.Lock()
    active_response = {"value": None}

    def is_cancelled():
        return (
            stop_event.is_set()
            or (
                external_cancel is not None
                and external_cancel.is_set()
            )
        )

    def publish(event_type, value=None):
        while not is_cancelled():
            try:
                event_queue.put(
                    (event_type, value),
                    timeout=0.1,
                )
                return True
            except queue.Full:
                continue

        return False

    def close_response():
        with response_lock:
            response = active_response["value"]

        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def read_response():
        try:
            if is_cancelled():
                return

            with requests.post(
                endpoint,
                json=request_payload,
                stream=True,
                timeout=(10, read_timeout),
            ) as response:

                with response_lock:
                    active_response["value"] = response

                if is_cancelled():
                    return

                response.raise_for_status()

                for raw_line in response.iter_lines(
                    chunk_size=1024
                ):
                    if is_cancelled():
                        break

                    if not raw_line:
                        continue

                    try:
                        event = json.loads(raw_line)

                    except (
                        json.JSONDecodeError,
                        UnicodeDecodeError,
                    ) as error:
                        raise OllamaStreamError(
                            "Evento JSON inválido recebido "
                            "do Ollama."
                        ) from error

                    if not isinstance(event, dict):
                        raise OllamaStreamError(
                            "Evento inesperado recebido "
                            "do Ollama."
                        )

                    if not publish("event", event):
                        break

                    if event.get("done") is True:
                        break

        except Exception as error:
            if not is_cancelled():
                publish("error", error)

        finally:
            with response_lock:
                active_response["value"] = None

            publish("finished")

    def extract_fragment(event):
        fragment = event.get("response")

        if fragment is None:
            message = event.get("message") or {}

            if isinstance(message, dict):
                fragment = message.get("content", "")

        return fragment if isinstance(fragment, str) else ""

    def extract_metrics(event):
        elapsed = time.monotonic() - started_at

        eval_count = event.get("eval_count")
        eval_duration = event.get("eval_duration")

        tokens_per_second = None

        if (
            isinstance(eval_count, (int, float))
            and isinstance(eval_duration, (int, float))
            and eval_duration > 0
        ):
            tokens_per_second = (
                eval_count * 1_000_000_000 / eval_duration
            )

        ttft = None

        if first_fragment_at is not None:
            ttft = first_fragment_at - started_at

        def ns_to_seconds(value):
            if isinstance(value, (int, float)):
                return value / 1_000_000_000
            return None

        return {
            "label": label,
            "elapsed_seconds": elapsed,
            "ttft_seconds": ttft,
            "fragment_count": fragment_count,
            "prompt_tokens": event.get("prompt_eval_count"),
            "output_tokens": eval_count,
            "tokens_per_second": tokens_per_second,
            "total_duration_seconds": ns_to_seconds(
                event.get("total_duration")
            ),
            "load_duration_seconds": ns_to_seconds(
                event.get("load_duration")
            ),
            "prompt_eval_seconds": ns_to_seconds(
                event.get("prompt_eval_duration")
            ),
            "generation_seconds": ns_to_seconds(
                eval_duration
            ),
            "done_reason": event.get("done_reason"),
        }

    def print_progress(force=False):
        nonlocal last_progress_at

        if not show_progress:
            return

        now = time.monotonic()

        if (
            not force
            and now - last_progress_at < progress_interval
        ):
            return

        elapsed = now - started_at

        print(
            f"\r\033[K[{label}] "
            f"Fragmentos: {fragment_count} | "
            f"Tempo: {elapsed:.1f}s | "
            "Ctrl+C para cancelar",
            end="",
            flush=True,
        )

        last_progress_at = now

    def print_summary(data):
        if not show_progress:
            return

        print(f"\r\033[K[{label}] Geração concluída.")

        print(
            f"[{label}] Tempo total: "
            f"{data['elapsed_seconds']:.2f}s"
        )

        print(
            f"[{label}] Fragmentos: "
            f"{data['fragment_count']}"
        )

        if data["ttft_seconds"] is not None:
            print(
                f"[{label}] Primeiro fragmento: "
                f"{data['ttft_seconds']:.2f}s"
            )

        if data["prompt_tokens"] is not None:
            print(
                f"[{label}] Tokens de entrada: "
                f"{data['prompt_tokens']}"
            )

        if data["output_tokens"] is not None:
            print(
                f"[{label}] Tokens de saída: "
                f"{data['output_tokens']}"
            )

        if data["tokens_per_second"] is not None:
            print(
                f"[{label}] Velocidade: "
                f"{data['tokens_per_second']:.2f} tokens/s"
            )

        if data["load_duration_seconds"] is not None:
            print(
                f"[{label}] Carregamento: "
                f"{data['load_duration_seconds']:.2f}s"
            )

        if data["generation_seconds"] is not None:
            print(
                f"[{label}] Geração efetiva: "
                f"{data['generation_seconds']:.2f}s"
            )

    reader = threading.Thread(
        target=read_response,
        name=f"{label}-ollama-stream",
        daemon=True,
    )

    if show_progress:
        print(
            f"[{label}] Iniciando geração... "
            "Ctrl+C para cancelar.",
            flush=True,
        )

    reader.start()

    try:
        while True:

            if external_cancel is not None:
                if external_cancel.is_set():
                    raise GenerationCancelled(
                        "".join(fragments)
                    )

            try:
                message_type, value = event_queue.get(
                    timeout=0.1
                )

            except queue.Empty:
                continue

            if message_type == "error":
                raise value

            if message_type == "finished":
                break

            if message_type != "event":
                continue

            event = value

            if event.get("error"):
                raise OllamaStreamError(
                    f"Ollama retornou erro: {event['error']}"
                )

            fragment = extract_fragment(event)

            if fragment:
                fragments.append(fragment)
                fragment_count += 1

                now = time.monotonic()

                if first_fragment_at is None:
                    first_fragment_at = now

                if on_fragment is not None:
                    on_fragment(fragment)

                print_progress()

            if event.get("done") is True:
                completed = True

                metrics = extract_metrics(event)

                print_summary(metrics)

                if on_metrics is not None:
                    on_metrics(metrics)

                break

    except KeyboardInterrupt as error:
        if show_progress:
            print(
                f"\n[{label}] Cancelado pelo usuário.",
                flush=True,
            )

        raise GenerationCancelled(
            "".join(fragments)
        ) from error

    finally:
        stop_event.set()

        close_response()

        if reader.is_alive():
            reader.join(timeout=1.0)

    if external_cancel is not None:
        if external_cancel.is_set():
            raise GenerationCancelled(
                "".join(fragments)
            )

    if not completed:
        raise OllamaStreamError(
            "O stream foi encerrado antes de receber "
            "o evento final 'done=True'."
        )

    return "".join(fragments)

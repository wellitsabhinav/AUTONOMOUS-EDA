"""Structured inference over a fixture, loopback Chat API, or OpenAI Responses."""

import http.client
import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from ..knowledge.index import canonical, identity
from .contracts import SYSTEM, VERSION, Context, selection_schema, unique_object

Adapter = Literal["disabled", "mock", "local", "openai"]
MAX_RESPONSE = 64 * 1024
OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"


class InferenceError(ValueError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.retryable = retryable


@dataclass(frozen=True)
class Route:
    adapter: Adapter = "disabled"
    model: str = ""
    endpoint: str = ""
    input_microdollars_per_million: int = 0
    output_microdollars_per_million: int = 0

    def __post_init__(self) -> None:
        if self.adapter not in {"disabled", "mock", "local", "openai"}:
            raise ValueError("Unsupported model adapter")
        if self.adapter in {"local", "openai"} and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}", self.model
        ):
            raise ValueError("A valid model name is required")
        if self.adapter == "local":
            local_address(self.endpoint)
        elif self.endpoint:
            raise ValueError("An endpoint can only be specified for the local adapter")
        rates = (self.input_microdollars_per_million, self.output_microdollars_per_million)
        if any(type(rate) is not int or not 0 <= rate <= 1_000_000_000 for rate in rates):
            raise ValueError("Invalid hosted token rates")
        if self.adapter == "openai" and not all(rates):
            raise ValueError("Configure input and output rates for hosted budget accounting")
        if self.adapter != "openai" and any(rates):
            raise ValueError("Token rates apply to the hosted adapter")

    @property
    def model_id(self) -> str:
        return "fixture/selection-v1" if self.adapter == "mock" else self.model

    def cost(self, input_tokens: int, output_tokens: int) -> int:
        total = (
            input_tokens * self.input_microdollars_per_million
            + output_tokens * self.output_microdollars_per_million
        )
        return (total + 999_999) // 1_000_000


def local_address(endpoint: str) -> tuple[str, int]:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.port is None
        or parsed.path != "/v1/chat/completions"
        or parsed.netloc not in {f"127.0.0.1:{parsed.port}", f"[::1]:{parsed.port}"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an explicit loopback HTTP /v1/chat/completions endpoint")
    return parsed.hostname, parsed.port


@dataclass(frozen=True)
class Request:
    body: bytes
    context_digest: str
    output_tokens: int
    data_class: str = "private_engineering"

    @property
    def input_reserve(self) -> int:
        # Bound reservation by serialized bytes plus allowance for chat framing.
        return len(self.body) + 256


@dataclass(frozen=True)
class Response:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    reported_model: str | None = None


class Provider(Protocol):
    def complete(self, request: Request, timeout: float) -> Response: ...


def request_for(route: Route, context: Context, maximum: int, *, repair: bool) -> Request:
    prompt = context.prompt(repair=repair)
    schema = selection_schema(context)
    if route.adapter == "openai":
        body = {
            "model": route.model,
            "input": prompt,
            "instructions": SYSTEM,
            "store": False,
            "max_output_tokens": maximum,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "engineering_selection",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
    else:
        body = {
            "model": route.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": maximum,
            "response_format": {"type": "json_object", "schema": schema},
        }
    return Request(canonical(body).encode(), identity(SYSTEM, prompt, schema, VERSION), maximum)


class Mock:
    """Deterministic fixture, with an optional scripted response for tests."""

    def __init__(self, context: Context, fixture: str | None = None) -> None:
        self.context = context
        self.fixture = fixture

    def complete(self, request: Request, timeout: float) -> Response:
        return Response(
            self.fixture
            if self.fixture is not None
            else canonical(
                {
                    "interpretation": self.context.interpretations[0]["id"],
                    "hypotheses": [c["id"] for c in self.context.hypotheses[:1]],
                    "next_measurements": [c["id"] for c in self.context.next_measurements],
                }
            )
        )


def count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 10_000_000 else None


def decode_response(body: bytes, *, hosted: bool) -> Response:
    try:
        data = json.loads(body, object_pairs_hook=unique_object)
        if hosted:
            if data["status"] != "completed":
                raise InferenceError("provider_incomplete")
            texts = [
                item["text"]
                for message in data["output"]
                if message["type"] == "message"
                for item in message["content"]
                if item["type"] == "output_text"
            ]
            if len(texts) != 1:
                raise InferenceError("provider_refused_or_incomplete")
            text = texts[0]
        else:
            (choice,) = data["choices"]
            if choice.get("finish_reason") not in {"stop", None}:
                raise InferenceError("provider_incomplete")
            text = choice["message"]["content"]
        if not isinstance(text, str):
            raise TypeError
        usage = data.get("usage") or {}
        input_tokens = count(usage.get("input_tokens" if hosted else "prompt_tokens"))
        output_tokens = count(usage.get("output_tokens" if hosted else "completion_tokens"))
        name = data.get("model")
        name = name if isinstance(name, str) and re.fullmatch(r"[\w./:-]{1,128}", name) else None
        return Response(text, input_tokens, output_tokens, name)
    except InferenceError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise InferenceError("malformed_provider_response", retryable=True) from None


@dataclass(frozen=True)
class HttpProvider:
    route: Route
    consent: bool = False
    hosted_enabled: bool = False

    def complete(self, request: Request, timeout: float) -> Response:
        # Budget wall time even when a peer slowly sends HTTP headers or body bytes.
        # The connection's socket also carries a timeout. A timed-out worker never retries.
        result: queue.Queue[Response | InferenceError] = queue.Queue(maxsize=1)
        hosted = self.route.adapter == "openai"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if hosted:
            if (
                not self.hosted_enabled
                or not self.consent
                or request.data_class != "private_engineering"
            ):
                raise InferenceError("hosted_consent_required")
            key = os.environ.get("OPENAI_API_KEY")
            if not key or not key.isascii() or not key.isprintable() or len(key) > 512:
                raise InferenceError("hosted_credentials_unavailable")
            headers["Authorization"] = f"Bearer {key}"
            address = urlsplit(OPENAI_ENDPOINT)
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                cast(str, address.hostname), timeout=timeout
            )
            path = address.path
        elif self.route.adapter == "local":
            host, port = local_address(self.route.endpoint)
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
            path = "/v1/chat/completions"
        else:
            raise InferenceError("inference_disabled")

        def call() -> None:
            deadline = time.monotonic() + timeout
            try:
                connection.connect()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                connection.request("POST", path, request.body, headers)
                with connection.getresponse() as response:
                    if response.status != 200:
                        raise InferenceError(
                            "provider_http_error",
                            retryable=response.status
                            in {
                                408,
                                429,
                                500,
                                502,
                                503,
                                504,
                            },
                        )
                    raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise InferenceError("provider_response_too_large")
                result.put(decode_response(raw, hosted=hosted))
            except InferenceError as error:
                result.put(error)
            except TimeoutError:
                result.put(InferenceError("provider_timeout", retryable=not hosted))
            except (OSError, http.client.HTTPException, ValueError):
                result.put(InferenceError("provider_unavailable", retryable=not hosted))
            finally:
                connection.close()

        threading.Thread(target=call, daemon=True).start()
        try:
            answer = result.get(timeout=timeout)
        except queue.Empty:
            raise InferenceError("provider_timeout", retryable=not hosted) from None
        if isinstance(answer, InferenceError):
            raise answer
        return answer

"""Local sentence embeddings and their persistent representation."""

import hashlib
import importlib
import math
import re
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

BATCH = 16
MAX_DIMENSION = 4096
MINILM_REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
MODEL_FILES = {
    "model.onnx": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
}


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


def terms(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def normalized(values: Sequence[float], dimension: int) -> tuple[float, ...]:
    if len(values) != dimension or not 1 <= dimension <= MAX_DIMENSION:
        raise ValueError("Embedding dimension mismatch")
    length = math.hypot(*values)
    if not math.isfinite(length) or not length:
        raise ValueError("Embedding must be finite and nonzero")
    return tuple(value / length for value in values)


def pack(values: Sequence[float], dimension: int) -> bytes:
    return struct.pack(f"<{dimension}f", *normalized(values, dimension))


def unpack(blob: bytes, dimension: int) -> tuple[float, ...]:
    if len(blob) != dimension * 4 or not 1 <= dimension <= MAX_DIMENSION:
        raise ValueError("Stored embedding dimension mismatch")
    values = struct.unpack(f"<{dimension}f", blob)
    if not math.isclose(math.hypot(*values), 1, abs_tol=1e-5):
        raise ValueError("Invalid stored embedding")
    return values


class TestEmbedder:
    """Lexical feature hashing for repeatable tests only."""

    __test__ = False
    model_id = "test/feature-hash-v1"
    dimension = 128

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        batch = []
        for text in texts:
            vector = [0.0] * self.dimension
            for word in terms(text) or ["_"]:
                index = hashlib.sha256(word.encode()).digest()[0] % self.dimension
                vector[index] += 1
            batch.append(vector)
        return batch


class MiniLM:
    model_id = f"sentence-transformers/all-MiniLM-L6-v2@{MINILM_REVISION}:onnx-mean-256-v1"
    dimension = 384

    def __init__(self, directory: Path) -> None:
        for name, expected in MODEL_FILES.items():
            try:
                with (directory / name).open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError as error:
                raise ValueError(
                    "Install the pinned MiniLM files locally. See docs/KNOWLEDGE.md"
                ) from error
            if actual != expected:
                raise ValueError(f"MiniLM file hash mismatch: {name}")
        try:
            self.numpy = importlib.import_module("numpy")
            runtime = importlib.import_module("onnxruntime")
            tokenizers = importlib.import_module("tokenizers")
        except ImportError as error:
            raise ValueError("Install the optional autonomous-eda[knowledge] extra") from error
        options = runtime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        runtime.disable_telemetry_events()
        self.session = runtime.InferenceSession(
            str(directory / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.tokenizer = tokenizers.Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not 1 <= len(texts) <= BATCH:
            raise ValueError(f"Encode between 1 and {BATCH} texts per batch")
        encodings = self.tokenizer.encode_batch(list(texts))
        inputs = {
            key: self.numpy.asarray([getattr(item, attr) for item in encodings], dtype="int64")
            for key, attr in (
                ("input_ids", "ids"),
                ("attention_mask", "attention_mask"),
                ("token_type_ids", "type_ids"),
            )
            if key in self.input_names
        }
        output = self.session.run(["last_hidden_state"], inputs)[0]
        mask = inputs["attention_mask"][..., None]
        pooled = (output * mask).sum(axis=1) / mask.sum(axis=1)
        result: list[list[float]] = pooled.tolist()
        return result

import ipaddress
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def configured_embedder():
    return OllamaEmbedder(os.environ.get("GAIDEN_CORPUS_EMBEDDING_MODEL", ""),
                          os.environ.get("GAIDEN_CORPUS_EMBEDDING_DIGEST", ""),
                          int(os.environ.get("GAIDEN_CORPUS_EMBEDDING_DIMENSION", "0")))


class QueryEmbedder:
    """Keep the generic WriterEngine query API aligned with Nomic documents."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.model = delegate.model
        self.dimension = delegate.dimension
        self.model_digest = delegate.model_digest

    def embed(self, texts):
        return self.delegate.embed(["search_query: " + text for text in texts])


class OllamaEmbedder:
    def __init__(self, model, model_digest, dimension):
        self.model, self.model_digest, self.dimension = model, model_digest, dimension
        if not model or any(word in model.casefold() for word in ("qwen", "phi", "openai", "gpt", "bm25")):
            raise ValueError("Select an allowed dense local embedding model")
        self.base = os.environ.get("GAIDEN_LOCAL_MODEL_URL", "http://127.0.0.1:11434").rstrip("/")
        parsed = urlsplit(self.base)
        if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment):
            raise ValueError("Use an unauthenticated loopback Ollama endpoint")
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname == "localhost"
        if not local:
            raise ValueError("Remote embedding endpoints are forbidden")
        with urlopen(self.base + "/api/tags", timeout=15) as response:
            models = json.load(response).get("models", [])
        if not any(m.get("name") == model and m.get("digest") == model_digest for m in models):
            raise ValueError("Installed embedding model digest does not match approved revision")

    def embed(self, texts):
        vectors = []
        for start in range(0, len(texts), 16):
            payload = {"model": self.model, "input": texts[start:start + 16], "truncate": False,
                       "keep_alive": "5m"}
            request = Request(self.base + "/api/embed", data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=300) as response:
                result = json.load(response)
            rows = result.get("embeddings", [])
            if len(rows) != len(payload["input"]) or any(len(v) != self.dimension for v in rows):
                raise ValueError("Incomplete or incompatible dense embedding response")
            vectors.extend(rows)
        return vectors

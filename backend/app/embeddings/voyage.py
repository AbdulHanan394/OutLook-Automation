from dataclasses import dataclass
import httpx


@dataclass
class EmbeddingResponse:
    vectors: list[list[float]]
    total_tokens: int


class VoyageClient:
    def __init__(self, base_url: str, api_key: str, model: str, dimension: int) -> None:
        self.base_url, self.api_key, self.model, self.dimension = base_url.rstrip("/"), api_key, model, dimension

    async def embed(self, texts: list[str], input_type: str) -> EmbeddingResponse:
        if len(texts) > 1000:
            raise ValueError("embedding batches cannot exceed 1000 texts")
        payload = {"input": texts, "model": self.model, "input_type": input_type, "output_dimension": self.dimension}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/embeddings", json=payload, headers={"Authorization": f"Bearer {self.api_key}"})
            response.raise_for_status()
        data = response.json()
        return EmbeddingResponse([item["embedding"] for item in data["data"]], data.get("usage", {}).get("total_tokens", 0))

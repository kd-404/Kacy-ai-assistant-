"""
RAG ingestion: demo-company/*.md -> chunks -> embeddings -> Azure AI Search index.

Run manually after `azd up` and after you've filled in SEARCH_* and an
embedding deployment in .env:

    python -m app.rag.ingest

Recreates the video's RAG pipeline step for step: Policy Documents ->
Embeddings -> Vector Database.
"""
from __future__ import annotations

import os
import pathlib

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

DEMO_DOCS_DIR = pathlib.Path(__file__).resolve().parents[3] / "demo-company"
CHUNK_SIZE_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_SIZE_CHARS
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP_CHARS
    return [c.strip() for c in chunks if c.strip()]


def embed(texts: list[str]) -> list[list[float]]:
    """
    Plug in your embedding call here (Foundry `text-embedding-3-small`
    deployment via the same AIProjectClient pattern as app/agent/client.py).
    Left unimplemented so this script fails loudly instead of indexing
    fake vectors.
    """
    raise NotImplementedError(
        "Wire up an embeddings deployment and call it here before running ingest.py"
    )


def ensure_index(index_client: SearchIndexClient, name: str, dims: int) -> None:
    index = SearchIndex(
        name=name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
            SearchField(name="content", type=SearchFieldDataType.String),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=dims,
                vector_search_profile_name="kacy-vector-profile",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="kacy-hnsw")],
            profiles=[
                VectorSearchProfile(
                    name="kacy-vector-profile", algorithm_configuration_name="kacy-hnsw"
                )
            ],
        ),
    )
    index_client.create_or_update_index(index)


def main() -> None:
    endpoint = os.environ["SEARCH_ENDPOINT"]
    key = os.environ["SEARCH_API_KEY"]
    index_name = os.environ.get("SEARCH_INDEX_NAME", "kacy-handbook")
    credential = AzureKeyCredential(key)

    docs = []
    for path in sorted(DEMO_DOCS_DIR.glob("*.md")):
        for i, chunk in enumerate(chunk_text(path.read_text(encoding="utf-8"))):
            docs.append({"id": f"{path.stem}-{i}", "source": path.name, "content": chunk})

    if not docs:
        raise SystemExit(f"No docs found in {DEMO_DOCS_DIR} — add demo company markdown first.")

    vectors = embed([d["content"] for d in docs])
    for doc, vector in zip(docs, vectors):
        doc["embedding"] = vector

    index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
    ensure_index(index_client, index_name, dims=len(vectors[0]))

    search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)
    search_client.upload_documents(documents=docs)
    print(f"Indexed {len(docs)} chunks from {len(list(DEMO_DOCS_DIR.glob('*.md')))} docs into {index_name}")


if __name__ == "__main__":
    main()

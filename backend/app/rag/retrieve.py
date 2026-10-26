"""
Retrieval side of RAG: given a user query, pull the most relevant chunks from
Azure AI Search so the agent answers from the handbook instead of guessing
(the video's "30 paid vacation days" hallucination this is meant to prevent).
"""
from __future__ import annotations

import os

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

from app.rag.ingest import embed


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    endpoint = os.environ["SEARCH_ENDPOINT"]
    key = os.environ["SEARCH_API_KEY"]
    index_name = os.environ.get("SEARCH_INDEX_NAME", "kacy-handbook")

    client = SearchClient(endpoint=endpoint, index_name=index_name, credential=AzureKeyCredential(key))
    [query_vector] = embed([query])

    results = client.search(
        search_text=query,
        vector_queries=[
            VectorizedQuery(vector=query_vector, k_nearest_neighbors=top_k, fields="embedding")
        ],
        select=["id", "source", "content"],
        top=top_k,
    )
    return [{"id": r["id"], "source": r["source"], "content": r["content"]} for r in results]

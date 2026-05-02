from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalSettings:
    api_host: str = "127.0.0.1"
    api_port: int = 8001
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000


def get_retrieval_settings() -> RetrievalSettings:
    return RetrievalSettings(
        api_host=os.getenv("RETRIEVAL_API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("RETRIEVAL_API_PORT", "8001")),
        mcp_host=os.getenv("RETRIEVAL_MCP_HOST", "127.0.0.1"),
        mcp_port=int(os.getenv("RETRIEVAL_MCP_PORT", "8000")),
    )


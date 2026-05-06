"""Halifax Bylaw Advisor — SaaS web app backend.

This package is the customer-facing layer. It composes Layer 1's bylaw
ingest data and Layer 2's retrieval into an LLM-driven chat experience
for architects and planning consultants. The MCP server in
``mcp/bylaw_retrieval`` continues to exist as a parallel surface for
power users; this package is the primary product.

Sub-packages:
- ``advisor.llm``: provider-agnostic gateway around Anthropic / future
  LLM providers. Hides messages/content/tool shapes behind a unified
  interface so the chat backend can switch providers without churn.
"""

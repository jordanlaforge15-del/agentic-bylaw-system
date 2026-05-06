# Installing the Halifax Bylaw Advisor in Claude Desktop

Three steps. Time to first answer: about 3 minutes.

## 1. Add the bylaw-retrieval MCP server to Claude Desktop

Open Claude Desktop's config file. Path by OS:

- **macOS**:    `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**:  `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**:    `~/.config/Claude/claude_desktop_config.json`

Add this entry under `mcpServers` (creating the key if missing):

```json
{
  "mcpServers": {
    "halifax-bylaw-advisor": {
      "url": "https://YOUR_HOSTED_URL/mcp",
      "transport": "streamable-http"
    }
  }
}
```

Replace `YOUR_HOSTED_URL` with the URL you received when you signed up.

> **Note on MCP transports**: this guide assumes Claude Desktop's
> support for streamable-HTTP MCP servers. If your build of Claude
> Desktop only supports stdio MCP servers, run the local bridge
> client we provide and point Claude Desktop at it via `command` /
> `args` instead. See `claude_desktop_config.example.json` for both
> shapes.

## 2. Create a Claude Project with the persona

In Claude.ai (web) or Claude Desktop:

1. Create a new Project named "Halifax Bylaw Advisor".
2. Open `persona.md` in this folder, copy its contents *below* the
   `---` separator (the actual prompt; the lines above are just
   instructions to you).
3. Paste it into the project's "Project instructions" field.
4. Save.

The Project will inherit the MCP servers configured in step 1, so
it has access to `search_bylaw_evidence`, `lookup_citation`,
`list_documents`, and `get_document_outline` automatically.

## 3. Restart Claude Desktop

Fully quit and relaunch the app so it loads the new MCP server
config and the new Project.

Open the "Halifax Bylaw Advisor" Project and ask a property
question to verify:

> *"What's the maximum building envelope at 6321 Quinpool Road?"*

Expected response shape: a structured envelope with zone (CEN-2),
max height (90 m), FAR (6.0), setbacks, overlays, and citations to
the specific schedules. If you get a vague text-only answer instead,
the MCP isn't loaded — re-check step 1.

## Troubleshooting

**"The geocoder isn't resolving the address"** — the response should
have populated `linked_datasets[*].location_confidence`. If it's
below 0.85 you'll see a note in the response; the assistant will
also recommend confirming via HRM's mapping tools.

**"The assistant is answering generally, not for the specific
property"** — the Claude client may not be populating the structured
`location` field on tool calls. Check the response's `notes` array
— if it mentions a missing location, the assistant should self-
correct. If it doesn't self-correct, paste this addendum at the top
of the project instructions:

> *"When using the search_bylaw_evidence tool, ALWAYS populate the
> `location` field if the user's question references an address.
> Read the response's `notes` array — if it warns that the location
> was missing, immediately re-issue the call with the slot populated.
> Do not give a final answer until the spatial data is in your
> evidence."*

**"The assistant is making claims I can't find in the bylaw"** —
report the chat log. The persona is instructed to cite, and to say
"I'd need to look that up" when it doesn't have evidence; failures
here are bugs we want to know about.

## Limits of v1

- The MCP serves the **HRM Regional Centre Land Use By-law only**.
  Properties outside the Regional Centre LUB area aren't covered.
- The bylaw is the version effective as of the most recent ingest.
  Check the assistant's response for `effective_date` if you need to
  know exactly which amendment cycle you're querying against.
- The assistant does not predict variance, site-plan, or rezoning
  outcomes. It tells you what the bylaw text says and what process
  applies; council discretion is not modeled.

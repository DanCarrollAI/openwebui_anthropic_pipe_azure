# Freshservice Tool UX — Portable Snippet

> **Purpose:** Reusable code blocks for suppressing raw JSON tool output and
> routing Freshservice tool results to the citation/sources panel in the
> Anthropic pipe. Lift and shift into any future pipe version.
>
> **Base version:** `anthropic_pipe.py` v0.8.5-azure.6
> **Branch:** `feature/freshservice`
> **Deployed pipe:** [`anthropic_pipe_fresh.py`](https://github.com/DanCarrollAI/openwebui_anthropic_pipe_azure/blob/feature/freshservice/anthropic_pipe_fresh.py)
> **Tested with:** Claude Sonnet 4.5 on Azure

---

## What It Does

1. **Suppresses** raw JSON tool-result dropdowns for four Freshservice tools.
2. **Emits friendly status messages** (e.g. "Searching tickets...") in the
   status bar while the tools run.
3. **Routes results to the Sources/citation panel** (same UX as builtin
   `search_web`, `fetch_url`, etc.) with descriptive titles.

---

## 1 — Constant: `TOOLS_SUPPRESSED_TO_CITATION`

**Where:** Top-level, outside the `Pipe` class (after imports, before
`class Pipe:`).

```python
# ── Freshservice: suppress tool output from main chat ──────────────────
# Custom tools listed here get the same UX as builtin tools: friendly status,
# results in the reasoning summary/citation panel
# instead of expandable raw JSON in the stream. Add tool names here to suppress
# their output from the main chat and show in the reasoning summary section.
# Freshservice: search_tickets, get_ticket_conversations, add_ticket_note, get_ticket_stats
TOOLS_SUPPRESSED_TO_CITATION = frozenset(
    (
        "search_tickets",
        "get_ticket_conversations",
        "add_ticket_note",
        "get_ticket_stats",
    )
)
```

---

## 2 — Suppress Generic "Executing tool:" Status

**Where:** Inside `pipe()` (or equivalent streaming method), at the point where
a `content_block_start` with `type == "tool_use"` is handled and a status
message is emitted. The existing pipe already has a branch for
`tool_name in builtin_tools`. Extend it:

```python
# Check if it's a builtin or suppressed-citation tool (friendly status later)
if (
    tool_name in builtin_tools
    or tool_name in TOOLS_SUPPRESSED_TO_CITATION
):
    # Will emit friendly status after input arrives
    pass
else:
    # User-defined tool - emit generic status
    await emit_event_local(
        {
            "type": "status",
            "data": {
                "description": f"🔧 Executing tool: {tool_name}",
                "done": False,
            },
        }
    )
```

**What changed:** Added `or tool_name in TOOLS_SUPPRESSED_TO_CITATION` to the
guard so these tools skip the generic "Executing tool:" message and instead
get a friendly status in step 3.

---

## 3 — Friendly Status Messages

**Where:** Inside `pipe()`, after the tool input JSON has been fully
accumulated (i.e. after `content_block_stop` for the `tool_use` block, or
wherever the pipe emits friendly statuses for builtin tools). The existing
pipe has an `if tool_name in builtin_tools:` block with per-tool statuses.
Add an `elif` after it:

```python
elif (
    tool_name
    in TOOLS_SUPPRESSED_TO_CITATION
):
    if tool_name == "search_tickets":
        friendly_status = (
            "🎫 Searching tickets..."
        )
    elif (
        tool_name
        == "get_ticket_conversations"
    ):
        tid = tool_input.get(
            "ticket_id", ""
        )
        friendly_status = (
            f"🎫 Fetching conversations: #{tid}"
            if tid
            else "🎫 Fetching ticket conversations..."
        )
    elif tool_name == "add_ticket_note":
        tid = tool_input.get(
            "ticket_id", ""
        )
        friendly_status = (
            f"🎫 Adding note to ticket #{tid}..."
            if tid
            else "🎫 Adding note to ticket..."
        )
    elif tool_name == "get_ticket_stats":
        friendly_status = (
            "📊 Fetching ticket stats..."
        )
```

The `friendly_status` is then emitted as a status event (already exists in
the pipe for builtin tools):

```python
if friendly_status:
    await emit_event_local(
        {
            "type": "status",
            "data": {
                "description": friendly_status,
                "done": False,
            },
        }
    )
```

---

## 4 — Route Tool Results to Citation Panel

**Where:** Inside `pipe()`, after a tool result is received. The existing pipe
has a branch: `if tool_name in builtin_tools:` that calls
`_emit_builtin_tool_result_source(...)`. Extend the condition:

```python
elif (
    tool_name in builtin_tools
    or tool_name
    in TOOLS_SUPPRESSED_TO_CITATION
):
    # Builtin or suppressed custom tool - emit as citation (reasoning summary)
    await self._emit_builtin_tool_result_source(
        emit_event_local,
        tool_name,
        tool_input,
        result_str,
        is_error=is_error,
    )
```

This replaces the default behaviour (which would render an expandable
`<details>` block with raw JSON in the chat stream).

---

## 5 — `_emit_builtin_tool_result_source` Modifications

**Where:** Inside the `_emit_builtin_tool_result_source` method of the `Pipe`
class. Three changes:

### 5a — Handle preformatted markdown from custom tools

At the top of the `try` block, before existing JSON parsing:

```python
# Custom tools in TOOLS_SUPPRESSED_TO_CITATION return preformatted markdown
if tool_name in TOOLS_SUPPRESSED_TO_CITATION:
    result_display = (
        tool_result if isinstance(tool_result, str) else str(tool_result)
    )
```

And later, skip the JSON-formatting branch for these tools:

```python
if tool_name in TOOLS_SUPPRESSED_TO_CITATION:
    pass  # result_display already set above
elif tool_name == "search_web":
    # ... existing search_web formatting ...
```

### 5b — Add tool icons

In the `tool_icons` dict:

```python
tool_icons = {
    "search_web": "🔍",
    "fetch_url": "🌐",
    "query_knowledge_files": "📚",
    "memory_query": "🧠",
    "memory_add": "🧠",
    "search_tickets": "🎫",
    "get_ticket_conversations": "🎫",
    "add_ticket_note": "🎫",
    "get_ticket_stats": "📊",
}
icon = tool_icons.get(tool_name, "🔧")
```

### 5c — Citation source names

After the existing `source_name` branches for builtin tools, add:

```python
elif tool_name == "search_tickets":
    # Derive a meaningful title from the first line of the tool result,
    # e.g. "🎫 Ticket #148246 - Full Details"
    first_line = ""
    if isinstance(tool_result, str):
        for line in tool_result.splitlines():
            line = line.strip()
            if line:
                first_line = line
                break
    if first_line:
        # Strip leading emoji / bullet characters
        first_line = re.sub(r"^[^\w#]*", "", first_line)
        # Strip surrounding bold markers if present
        m = re.match(r"^\*\*(.+)\*\*$", first_line)
        if m:
            first_line = m.group(1)
        source_name = f"{icon} {first_line}"
    else:
        source_name = f"{icon} Search tickets"
elif tool_name == "get_ticket_conversations":
    tid = tool_input.get("ticket_id", "")
    source_name = (
        f"{icon} Ticket #{tid} conversations"
        if tid
        else f"{icon} Ticket conversations"
    )
elif tool_name == "add_ticket_note":
    tid = tool_input.get("ticket_id", "")
    source_name = (
        f"{icon} Note added to ticket #{tid}" if tid else f"{icon} Add note"
    )
elif tool_name == "get_ticket_stats":
    source_name = f"{icon} Ticket statistics"
```

---

## 6 — Pipe ID (Optional)

To run the Freshservice variant alongside the original pipe, change the `id`
field in the docstring header:

```python
"""
title: Anthropic API Integration (Azure Compatible)
id: anthropic_azure_fresh
...
"""
```

---

## 7 — System Prompt (`archie-system-prompt.md`)

A dedicated Archie system prompt is used for the Freshservice model.
See the full file in the repo:
[`archie-system-prompt.md`](https://github.com/DanCarrollAI/openwebui_anthropic_pipe_azure/blob/feature/freshservice/archie-system-prompt.md)

Key points:
- Defines 5 operating modes (Summary, Analyser, Investigation, Trends,
  General).
- Lists only the four tools the model can call.
- `add_ticket_note` is **disabled** (read-only access) — the system prompt
  tells the model never to call it.
- Cannot filter by client/company (Freshservice API limitation with
  current permissions). The prompt instructs the model to ask for ticket IDs
  or use other available filters instead.

---

## How to Apply to a New Pipe Version

1. Copy `TOOLS_SUPPRESSED_TO_CITATION` constant (section 1) above
   `class Pipe:`.
2. In the streaming method, find the `tool_name in builtin_tools` guard for
   status suppression and extend it (section 2).
3. Find the friendly-status `elif` chain for builtin tools and append the
   Freshservice block (section 3).
4. Find the tool-result routing `elif` for builtin tools and extend it
   (section 4).
5. In `_emit_builtin_tool_result_source`, add the three sub-changes
   (sections 5a, 5b, 5c).
6. Optionally change the pipe `id` (section 6).
7. Deploy `archie-system-prompt.md` as the model's system prompt in
   OpenWebUI (section 7).

To add more custom tools in future, add the tool name to
`TOOLS_SUPPRESSED_TO_CITATION` and optionally add a friendly status and
icon in the same places.

"""
title: Freshservice Tickets (Enhanced – Companies / Client Filter)
author: Assistant
version: 1.6.0
description: Query Freshservice tickets with assignee filtering, conversations, and company/client filter. The UI "Companies" filter is supported in two ways: (1) Workspaces API (Filter Clients) for MSP accounts; (2) Ticket Fields API – if "Companies" is a ticket field (default or custom), it is discovered from GET /api/v2/ticket_fields and used for Filter Tickets. Use list_companies to find a company by name, then search_tickets(company_id=...) or search_tickets(company_name='...').
"""

from typing import Callable, Any
from pydantic import BaseModel, Field
import requests
import re


class Tools:
    class Valves(BaseModel):
        FRESHSERVICE_API_KEY: str = Field(
            default="",
            description="Your Freshservice API Key",
        )
        FRESHSERVICE_DOMAIN: str = Field(
            default="instantonit",
            description="Your Freshservice domain (e.g., 'company' from company.freshservice.com)",
        )
        WORKSPACE_ID_NAMES: str = Field(
            default="",
            description="Optional. When Workspaces API returns 403, use this to map workspace_id to client name. Comma-separated id:Name, e.g. '2:Primary,3:Blume Equity'. Enables client name on tickets and list_companies/search by company name.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self._current_agent_id = None
        self._company_field_cache = None  # {"name": "<field_name>", "choices": [...]} from ticket_fields

    def _get_current_agent_id(self) -> int:
        """Get the agent ID associated with the API key"""
        if self._current_agent_id:
            return self._current_agent_id

        base_url = f"https://{self.valves.FRESHSERVICE_DOMAIN}.freshservice.com/api/v2"

        try:
            url = f"{base_url}/agents/me"
            response = requests.get(
                url,
                auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if response.status_code == 200:
                agent = response.json().get("agent", {})
                self._current_agent_id = agent.get("id")
                return self._current_agent_id
        except Exception as e:
            print(f"Error getting current agent: {str(e)}")

        return None

    def _get_base_url(self) -> str:
        return f"https://{self.valves.FRESHSERVICE_DOMAIN}.freshservice.com/api/v2"

    def _get_workspace_id_name_map(self):
        """Parse WORKSPACE_ID_NAMES valve into {id: name} and [(id, name), ...]. Returns (by_id, list_of_pairs)."""
        raw = (self.valves.WORKSPACE_ID_NAMES or "").strip()
        if not raw:
            return {}, []
        by_id = {}
        pairs = []
        for part in raw.split(","):
            part = part.strip()
            if ":" not in part:
                continue
            id_str, name = part.split(":", 1)
            id_str, name = id_str.strip(), name.strip()
            if not id_str or not name:
                continue
            try:
                wid = int(id_str)
                by_id[wid] = name
                pairs.append((wid, name))
            except ValueError:
                continue
        return by_id, pairs

    def _get_workspace_name(self, workspace_id) -> str:
        """Return client/workspace name for workspace_id if WORKSPACE_ID_NAMES is set, else None."""
        if workspace_id is None:
            return ""
        by_id, _ = self._get_workspace_id_name_map()
        return by_id.get(int(workspace_id) if isinstance(workspace_id, (int, float)) else workspace_id, "")

    def _resolve_workspace_id_from_mapping(self, company_name: str) -> int:
        """Resolve company name to workspace_id using WORKSPACE_ID_NAMES. Returns 0 if not found."""
        _, pairs = self._get_workspace_id_name_map()
        needle = (company_name or "").strip().lower()
        if not needle:
            return 0
        for wid, name in pairs:
            if needle in (name or "").lower() or (name or "").lower() in needle:
                return wid
        return 0

    # =====================================================
    # DIAGNOSTIC: See actual ticket field names/schema (for debugging company filter)
    # =====================================================
    def get_ticket_fields_schema(
        self,
        __user__: dict = {},
    ) -> str:
        """
        Diagnostic: fetch GET /api/v2/ticket_fields and return a short schema (name, label, type, choices).
        Use this to see which field backs the UI 'Companies' filter and what its exact name is.
        Also reports workspaces API status (200/403/404).
        """
        base_url = self._get_base_url()
        if not self.valves.FRESHSERVICE_API_KEY or not self.valves.FRESHSERVICE_DOMAIN:
            return "Error: Configure FRESHSERVICE_API_KEY and FRESHSERVICE_DOMAIN."

        auth = (self.valves.FRESHSERVICE_API_KEY, "X")
        headers = {"Content-Type": "application/json"}
        out = []

        # 1) Workspaces API status
        try:
            r = requests.get(f"{base_url}/workspaces", auth=auth, headers=headers, timeout=10)
            out.append(f"**Workspaces API** `GET /api/v2/workspaces`: status **{r.status_code}**")
            if r.status_code != 200:
                try:
                    body = r.json()
                    out.append(f"  Body: `{body}`")
                except Exception:
                    out.append(f"  Body (text): {r.text[:200]}")
        except Exception as e:
            out.append(f"**Workspaces API** error: {e}")

        # 2) Ticket fields schema
        try:
            r = requests.get(f"{base_url}/ticket_fields", auth=auth, headers=headers, timeout=10)
            out.append(f"\n**Ticket Fields API** `GET /api/v2/ticket_fields`: status **{r.status_code}**")
            if r.status_code != 200:
                try:
                    out.append(f"  Body: `{r.json()}`")
                except Exception:
                    out.append(f"  Body: {r.text[:200]}")
            else:
                data = r.json()
                fields = data.get("ticket_fields", [])
                if not fields:
                    out.append("  (no ticket_fields in response)")
                else:
                    out.append(f"  Found **{len(fields)}** fields. Fields with **choices** (candidate for Companies/client):\n")
                    for f in fields:
                        if not isinstance(f, dict):
                            continue
                        name = f.get("name") or "(no name)"
                        label = f.get("label") or "(no label)"
                        ftype = f.get("field_type") or ""
                        choices = f.get("choices") or []
                        if isinstance(choices, list) and len(choices) > 0:
                            sample = [str(c.get("value") or c.get("id")) for c in choices[:5]]
                            out.append(f"  - **name**: `{name}` | **label**: {label} | **type**: {ftype} | **choices**: {len(choices)} (e.g. {sample})")
                    out.append("\n  All field **names** (for Filter Tickets query):")
                    names = [f.get("name") for f in fields if isinstance(f, dict) and f.get("name")]
                    out.append("  " + ", ".join(f"`{n}`" for n in names[:40]))
                    if len(names) > 40:
                        out.append(f"  ... and {len(names) - 40} more")
        except Exception as e:
            out.append(f"\n**Ticket Fields API** error: {e}")

        return "\n".join(out)

    def _get_company_field_from_ticket_fields(self):
        """
        Discover the UI "Companies" field from GET /api/v2/ticket_fields (documented API).
        Returns None or {"name": "<field_name>", "choices": [{"id": n, "value": "Name"}, ...]}.
        Cached on self._company_field_cache so list_companies and search_tickets can use it.
        """
        if self._company_field_cache is not None:
            return self._company_field_cache
        base_url = self._get_base_url()
        try:
            resp = requests.get(
                f"{base_url}/ticket_fields",
                auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            fields = data.get("ticket_fields", [])
            for f in fields:
                if not isinstance(f, dict):
                    continue
                label = (f.get("label") or "").lower()
                name = (f.get("name") or "").lower()
                # Prefer department_name / Department for client/company filter (UI may show as "Companies")
                if "department_name" in name or "department" in label or "department" in name:
                    choices = f.get("choices") or []
                    if isinstance(choices, list) and len(choices) > 0:
                        out = {
                            "name": f.get("name"),
                            "choices": [c for c in choices if isinstance(c, dict) and ("id" in c or "value" in c)],
                        }
                        self._company_field_cache = out
                        return out
                # Fallback: company-related field
                if "compan" in label or "compan" in name or name == "company_id":
                    choices = f.get("choices") or []
                    if isinstance(choices, list) and len(choices) > 0:
                        out = {
                            "name": f.get("name"),
                            "choices": [c for c in choices if isinstance(c, dict) and ("id" in c or "value" in c)],
                        }
                        self._company_field_cache = out
                        return out
            self._company_field_cache = {}
            return None
        except Exception:
            return None

    # =====================================================
    # LIST CLIENTS – (1) Workspaces API for MSP, (2) Ticket Fields "Companies" if present
    # Docs: https://api.freshservice.com/v2/ – Filter Clients (Freshservice for MSPs)
    # GET /api/v2/workspaces?query="name:'Acme'" (query URL-encoded)
    # =====================================================
    def list_companies(
        self,
        name_contains: str = "",
        __user__: dict = {},
    ) -> str:
        """
        List clients (workspaces) in Freshservice. Uses the official Workspaces API
        (Filter Clients). In MSP, clients = workspaces. Use this to get workspace_id
        by name, then filter tickets with search_tickets(company_id=...) or
        search_tickets(company_name='...').
        :param name_contains: Optional. Filter by client name (exact or partial via query).
        :return: Formatted list of clients (id, name). Use company_id in search_tickets.
        """
        base_url = self._get_base_url()

        if not self.valves.FRESHSERVICE_API_KEY or not self.valves.FRESHSERVICE_DOMAIN:
            return "Error: Please configure Freshservice API Key and Domain in the tool settings."

        try:
            auth = (self.valves.FRESHSERVICE_API_KEY, "X")
            headers = {"Content-Type": "application/json"}
            # Official API: GET /api/v2/workspaces?query="name:'Acme'" (query must be URL-encoded, in double quotes, value in single quotes)
            url = f"{base_url}/workspaces"
            all_clients = []

            if name_contains and name_contains.strip():
                # Query by name (exact match per docs; use state:Active to avoid inactive)
                name_esc = name_contains.strip()[:100].replace("'", "\\'")
                query_val = f'"name:\'{name_esc}\'"'
                response = requests.get(
                    url,
                    auth=auth,
                    headers=headers,
                    params={"query": query_val},
                    timeout=10,
                )
            else:
                # List all: try active workspaces only (state is a supported client field)
                response = requests.get(
                    url,
                    auth=auth,
                    headers=headers,
                    params={"query": '"state:\'Active\'"'},
                    timeout=10,
                )
                if response.status_code != 200:
                    # Fallback: try without query (some accounts may not support query)
                    response = requests.get(url, auth=auth, headers=headers, timeout=10)

            if response.status_code in (404, 403):
                # 404 = no workspaces API; 403 = not authorized. Fallback: (1) WORKSPACE_ID_NAMES mapping, (2) ticket field
                _, pairs = self._get_workspace_id_name_map()
                if pairs:
                    for wid, name in pairs:
                        if name_contains and name_contains.strip():
                            if name_contains.strip().lower() not in (name or "").lower():
                                continue
                        all_clients.append({"id": wid, "name": name})
                    if all_clients:
                        result = "🏢 **Clients (from WORKSPACE_ID_NAMES)** — use company_id in search_tickets\n\n"
                        for c in all_clients[:50]:
                            result += f"- **{c['name']}** — `company_id`: {c['id']}\n"
                        if len(all_clients) > 50:
                            result += f"\n... and {len(all_clients) - 50} more."
                        return result
                company_field = self._get_company_field_from_ticket_fields()
                if company_field:
                    choices = company_field.get("choices", [])
                    for c in choices:
                        cid = c.get("id") if isinstance(c.get("id"), (int, float)) else c.get("value")
                        cname = c.get("value") if c.get("value") else str(c.get("id", ""))
                        if cid is not None and cname:
                            all_clients.append({"id": int(cid) if isinstance(cid, (int, float)) else cid, "name": str(cname)})
                    if name_contains and name_contains.strip():
                        needle = name_contains.strip().lower()
                        all_clients = [x for x in all_clients if needle in (x.get("name") or "").lower()]
                    if all_clients:
                        result = "🏢 **Companies** (from ticket field – use company_id in search_tickets)\n\n"
                        for c in all_clients[:50]:
                            result += f"- **{c['name']}** — `company_id`: {c['id']}\n"
                        if len(all_clients) > 50:
                            result += f"\n... and {len(all_clients) - 50} more."
                        return result
                return (
                    "Workspaces API returned 404/403 (not available or not authorized), and no company/client ticket field was found (looked for department_name/department or company-related fields with choices). "
                    "Run **get_ticket_fields_schema** to see all ticket field names and pick the one that backs the UI Companies filter; then we can align the tool to that field name."
                )
            if response.status_code != 200:
                return f"Error fetching workspaces (clients): {response.status_code} - {response.text}"

            data = response.json()
            # Response may be {"workspaces": [...]} or similar; support common keys
            workspaces = data.get("workspaces", data.get("clients", data.get("results", [])))
            if not isinstance(workspaces, list):
                workspaces = []

            for w in workspaces:
                if isinstance(w, dict):
                    all_clients.append({"id": w.get("id"), "name": w.get("name", "N/A")})
                elif hasattr(w, "get"):
                    all_clients.append({"id": w.get("id"), "name": w.get("name", "N/A")})

            if name_contains and name_contains.strip() and not all_clients:
                # Try listing all and filtering client-side (in case query is exact-only)
                resp2 = requests.get(url, auth=auth, headers=headers, timeout=10)
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    workspaces2 = data2.get("workspaces", data2.get("clients", data2.get("results", [])))
                    needle = name_contains.strip().lower()
                    for w in workspaces2 or []:
                        name = (w.get("name") if isinstance(w, dict) else getattr(w, "name", "")) or ""
                        if needle in name.lower():
                            wid = w.get("id") if isinstance(w, dict) else getattr(w, "id", None)
                            if wid is not None:
                                all_clients.append({"id": wid, "name": name})

            if not all_clients:
                if name_contains:
                    return f"No clients (workspaces) found matching '{name_contains}'. Try list_companies without name_contains to see all."
                return "No workspaces (clients) found. If you are not on Freshservice for MSPs, client filtering may not be available."

            result = "🏢 **Clients (workspaces)** — use company_id in search_tickets to filter by client\n\n"
            for c in all_clients[:50]:
                result += f"- **{c['name']}** — `company_id`: {c['id']}\n"
            if len(all_clients) > 50:
                result += f"\n... and {len(all_clients) - 50} more."
            return result

        except Exception as e:
            return f"Error listing workspaces (clients): {str(e)}"

    def _resolve_workspace_id(self, company_name: str) -> int:
        """Resolve client/company name to workspace_id via Workspaces API. Returns 0 if not found."""
        base_url = self._get_base_url()
        try:
            url = f"{base_url}/workspaces"
            auth = (self.valves.FRESHSERVICE_API_KEY, "X")
            headers = {"Content-Type": "application/json"}
            name_esc = company_name.strip()[:100].replace("'", "\\'")
            query_val = f'"name:\'{name_esc}\'"'
            response = requests.get(url, auth=auth, headers=headers, params={"query": query_val}, timeout=10)
            if response.status_code != 200:
                response = requests.get(url, auth=auth, headers=headers, timeout=10)
            if response.status_code != 200:
                return 0
            data = response.json()
            workspaces = data.get("workspaces", data.get("clients", data.get("results", [])))
            if not isinstance(workspaces, list):
                return 0
            needle = company_name.strip().lower()
            for w in workspaces:
                name = (w.get("name") if isinstance(w, dict) else "") or ""
                if needle in name.lower():
                    return w.get("id", 0) if isinstance(w, dict) else 0
            return 0
        except Exception:
            return 0

    def _resolve_company_id_from_ticket_field(self, company_name: str) -> int:
        """Resolve company name to ID using the 'Companies' ticket field choices (if any). Returns 0 if not found."""
        cf = self._get_company_field_from_ticket_fields()
        if not cf or not cf.get("choices"):
            return 0
        needle = (company_name or "").strip().lower()
        if not needle:
            return 0
        for c in cf["choices"]:
            val = (c.get("value") or str(c.get("id", "")) or "").strip().lower()
            if needle in val or val in needle:
                cid = c.get("id")
                if cid is not None:
                    return int(cid) if isinstance(cid, (int, float)) else cid
        return 0

    def search_tickets(
        self,
        status: str = "",
        priority: str = "",
        ticket_id: int = 0,
        assigned_to_me: bool = False,
        assignee_id: int = 0,
        company_id: int = 0,
        company_name: str = "",
        __user__: dict = {},
    ) -> str:
        """
        Search Freshservice tickets. You can search by status (open/pending/resolved/closed),
        priority (low/medium/high/urgent), specific ticket_id, filter by assignee, or by client/company.
        :param status: Ticket status to filter by
        :param priority: Ticket priority to filter by
        :param ticket_id: Specific ticket ID to retrieve
        :param assigned_to_me: If True, only show tickets assigned to you
        :param assignee_id: Filter by specific assignee/agent ID
        :param company_id: Filter tickets by company (client) ID. Use list_companies to get IDs.
        :param company_name: Filter tickets by company name (e.g. 'Blume Equity'). Resolved to company_id automatically.
        :return: Formatted ticket information
        """
        base_url = self._get_base_url()

        if not self.valves.FRESHSERVICE_API_KEY or not self.valves.FRESHSERVICE_DOMAIN:
            return "Error: Please configure Freshservice API Key and Domain in the tool settings."

        # Resolve company_name to ID (workspace or ticket field "Companies") if provided
        if company_name and not company_id:
            company_id = self._resolve_workspace_id(company_name)
            if not company_id:
                company_id = self._resolve_company_id_from_ticket_field(company_name)
            if not company_id:
                return f"Error: No company/client found matching '{company_name}'. Use list_companies to see available companies and exact names."

        try:
            if ticket_id > 0:
                url = f"{base_url}/tickets/{ticket_id}"
                response = requests.get(
                    url,
                    auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )

                if response.status_code == 200:
                    ticket = response.json().get("ticket", {})
                    return self._format_single_ticket(ticket)
                else:
                    return f"Error: Ticket #{ticket_id} not found. Status code: {response.status_code}"

            if assigned_to_me:
                assignee_id = self._get_current_agent_id()
                if not assignee_id:
                    return "Error: Could not determine your agent ID. Please check your API key."

            # Filter by company/client: (1) Ticket field "Companies" if present, else (2) workspace_id
            if company_id > 0:
                company_field = self._get_company_field_from_ticket_fields()
                filtered_tickets = []

                if company_field and company_field.get("name"):
                    # UI "Companies" is a ticket field – use Filter Tickets (documented)
                    field_name = company_field["name"]
                    filter_url = f"{base_url}/tickets/filter"
                    # Resolve company_id to value for dropdown (Filter: string in single quotes)
                    company_value = company_id
                    for c in company_field.get("choices") or []:
                        if (c.get("id") == company_id or
                                (isinstance(company_id, (int, float)) and c.get("id") is not None and int(c.get("id")) == int(company_id))):
                            company_value = c.get("value") or c.get("id")
                            break
                    page = 1
                    while True:
                        if isinstance(company_value, str):
                            esc = str(company_value).replace("'", "\\'")[:200]
                            query_val = f'"{field_name}:\'{esc}\'"'
                        else:
                            query_val = f'"{field_name}:{company_id}"'
                        response = requests.get(
                            filter_url,
                            auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                            params={"query": query_val, "page": page},
                            headers={"Content-Type": "application/json"},
                            timeout=15,
                        )
                        if response.status_code != 200:
                            break
                        data = response.json()
                        tickets = data.get("tickets", [])
                        if not tickets:
                            break
                        filtered_tickets.extend(tickets)
                        if len(tickets) < 30:
                            break
                        page += 1
                        if page > 20:
                            break
                else:
                    # Use workspace_id (MSP / accounts with workspaces)
                    list_url = f"{base_url}/tickets"
                    page = 1
                    per_page = 100
                    max_pages = 15
                    while page <= max_pages:
                        response = requests.get(
                            list_url,
                            auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                            params={"per_page": per_page, "page": page, "workspace_id": company_id},
                            headers={"Content-Type": "application/json"},
                            timeout=15,
                        )
                        if response.status_code != 200:
                            return f"Error fetching tickets: {response.status_code} - {response.text}"
                        all_tickets = response.json().get("tickets", [])
                        if not all_tickets:
                            break
                        filtered_tickets.extend(all_tickets)
                        if len(all_tickets) < per_page:
                            break
                        page += 1

                if filtered_tickets:
                    if status:
                        status_map = {"open": 2, "pending": 3, "resolved": 4, "closed": 5}
                        status_num = status_map.get(status.lower())
                        if status_num is not None:
                            filtered_tickets = [t for t in filtered_tickets if t.get("status") == status_num]
                    if priority:
                        priority_map = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
                        priority_num = priority_map.get(priority.lower())
                        if priority_num is not None:
                            filtered_tickets = [t for t in filtered_tickets if t.get("priority") == priority_num]
                    if assignee_id > 0:
                        filtered_tickets = [t for t in filtered_tickets if t.get("responder_id") == assignee_id]
                    return self._format_tickets(filtered_tickets, assigned_to_me, company_id=company_id)
                return f"No tickets found for company_id {company_id}. Use list_companies to confirm name and ID."

            # No company filter – original behaviour
            url = f"{base_url}/tickets"
            params = {"per_page": 100}

            response = requests.get(
                url,
                auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                params=params,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if response.status_code == 200:
                all_tickets = response.json().get("tickets", [])
                filtered_tickets = all_tickets

                if status:
                    status_map = {"open": 2, "pending": 3, "resolved": 4, "closed": 5}
                    status_num = status_map.get(status.lower())
                    if status_num:
                        filtered_tickets = [
                            t for t in filtered_tickets if t.get("status") == status_num
                        ]

                if priority:
                    priority_map = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
                    priority_num = priority_map.get(priority.lower())
                    if priority_num:
                        filtered_tickets = [
                            t
                            for t in filtered_tickets
                            if t.get("priority") == priority_num
                        ]

                if assignee_id > 0:
                    filtered_tickets = [
                        t
                        for t in filtered_tickets
                        if t.get("responder_id") == assignee_id
                    ]

                return self._format_tickets(filtered_tickets, assigned_to_me)
            else:
                return (
                    f"Error fetching tickets: {response.status_code} - {response.text}"
                )

        except Exception as e:
            return f"Error querying Freshservice: {str(e)}"

    def get_ticket_conversations(
        self,
        ticket_id: int,
        include_private: bool = True,
        __user__: dict = {},
    ) -> str:
        """
        Get all conversations (notes and replies) for a specific ticket, including private notes.
        :param ticket_id: The ticket ID to get conversations for
        :param include_private: If True, include private notes (default: True)
        :return: Formatted list of all conversations and notes
        """
        base_url = self._get_base_url()

        if not self.valves.FRESHSERVICE_API_KEY or not self.valves.FRESHSERVICE_DOMAIN:
            return "Error: Please configure Freshservice API Key and Domain in the tool settings."

        if ticket_id <= 0:
            return "Error: Please provide a valid ticket ID."

        try:
            ticket_url = f"{base_url}/tickets/{ticket_id}"
            ticket_response = requests.get(
                ticket_url,
                auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            ticket_info = ""
            if ticket_response.status_code == 200:
                ticket = ticket_response.json().get("ticket", {})
                ticket_info = (
                    f"🎫 **Ticket #{ticket_id}:** {ticket.get('subject', 'N/A')}\n\n"
                )

            conversations_url = f"{base_url}/tickets/{ticket_id}/conversations"
            response = requests.get(
                conversations_url,
                auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if response.status_code == 200:
                conversations = response.json().get("conversations", [])

                if not conversations:
                    return f"{ticket_info}📭 **No conversations or notes found for this ticket.**"

                return ticket_info + self._format_conversations(
                    conversations, include_private
                )

            elif response.status_code == 404:
                return f"Error: Ticket #{ticket_id} not found."
            else:
                return f"Error fetching conversations: {response.status_code} - {response.text}"

        except Exception as e:
            return f"Error querying Freshservice conversations: {str(e)}"

    def add_ticket_note(
        self,
        ticket_id: int,
        body: str,
        private: bool = True,
        __user__: dict = {},
    ) -> str:
        """
        Add a note to an existing ticket.
        :param ticket_id: The ticket ID to add a note to
        :param body: The content of the note
        :param private: If True, the note is private (only visible to agents). Default: True
        :return: Confirmation message
        """
        base_url = self._get_base_url()

        if not self.valves.FRESHSERVICE_API_KEY or not self.valves.FRESHSERVICE_DOMAIN:
            return "Error: Please configure Freshservice API Key and Domain in the tool settings."

        if ticket_id <= 0:
            return "Error: Please provide a valid ticket ID."

        if not body or not body.strip():
            return "Error: Note body cannot be empty."

        try:
            url = f"{base_url}/tickets/{ticket_id}/notes"
            payload = {"body": body, "private": private}

            response = requests.post(
                url,
                auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )

            if response.status_code in [200, 201]:
                note_type = "private" if private else "public"
                return f"✅ Successfully added {note_type} note to Ticket #{ticket_id}."
            elif response.status_code == 404:
                return f"Error: Ticket #{ticket_id} not found."
            else:
                return f"Error adding note: {response.status_code} - {response.text}"

        except Exception as e:
            return f"Error adding note to Freshservice: {str(e)}"

    def get_ticket_stats(
        self,
        __user__: dict = {},
    ) -> str:
        """
        Get overall ticket statistics including counts by status and priority.
        :return: Summary statistics of all tickets
        """
        base_url = self._get_base_url()

        if not self.valves.FRESHSERVICE_API_KEY or not self.valves.FRESHSERVICE_DOMAIN:
            return "Error: Please configure Freshservice API Key and Domain."

        try:
            url = f"{base_url}/tickets"
            params = {"per_page": 100}

            response = requests.get(
                url,
                auth=(self.valves.FRESHSERVICE_API_KEY, "X"),
                params=params,
                timeout=10,
            )

            if response.status_code == 200:
                tickets = response.json().get("tickets", [])

                total = len(tickets)
                open_count = sum(1 for t in tickets if t.get("status") == 2)
                pending_count = sum(1 for t in tickets if t.get("status") == 3)
                resolved_count = sum(1 for t in tickets if t.get("status") == 4)
                closed_count = sum(1 for t in tickets if t.get("status") == 5)
                high_priority = sum(1 for t in tickets if t.get("priority") >= 3)

                result = "📊 **Freshservice Ticket Statistics**\n\n"
                result += f"**Total Tickets:** {total}\n"
                result += f"**Open:** {open_count}\n"
                result += f"**Pending:** {pending_count}\n"
                result += f"**Resolved:** {resolved_count}\n"
                result += f"**Closed:** {closed_count}\n"
                result += f"**High/Urgent Priority:** {high_priority}\n"

                return result
            else:
                return f"Error: {response.status_code}"

        except Exception as e:
            return f"Error: {str(e)}"

    def _format_tickets(self, tickets: list, show_assigned_note: bool = False, company_id: int = 0) -> str:
        """Format multiple tickets"""
        if not tickets:
            return "No tickets found matching your criteria."

        result = f"**Found {len(tickets)} ticket(s)"
        if show_assigned_note:
            result += " assigned to you"
        if company_id:
            result += f" for company_id {company_id}"
        result += ":**\n\n"

        status_map = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
        priority_map = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}

        for ticket in tickets[:10]:
            result += f"🎫 **Ticket #{ticket.get('id')}**\n"
            result += f"   **Subject:** {ticket.get('subject', 'N/A')}\n"
            result += (
                f"   **Status:** {status_map.get(ticket.get('status'), 'Unknown')}\n"
            )
            result += f"   **Priority:** {priority_map.get(ticket.get('priority'), 'Unknown')}\n"
            result += f"   **Assigned to:** Agent ID {ticket.get('responder_id', 'Unassigned')}\n"
            result += f"   **Created:** {ticket.get('created_at', 'N/A')[:10]}\n"
            result += "\n"

        if len(tickets) > 10:
            result += f"\n... and {len(tickets) - 10} more tickets"

        return result

    def _format_single_ticket(self, ticket: dict) -> str:
        """Format single ticket with details"""
        status_map = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
        priority_map = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}

        result = f"🎫 **Ticket #{ticket.get('id')}** - Full Details\n\n"
        result += f"**Subject:** {ticket.get('subject', 'N/A')}\n"
        result += f"**Status:** {status_map.get(ticket.get('status'), 'Unknown')}\n"
        result += (
            f"**Priority:** {priority_map.get(ticket.get('priority'), 'Unknown')}\n"
        )
        result += f"**Type:** {ticket.get('type', 'N/A')}\n"
        result += f"**Category:** {ticket.get('category', 'N/A')}\n"
        wid = ticket.get("workspace_id")
        if wid is not None:
            client_name = self._get_workspace_name(wid)
            if client_name:
                result += f"**Client (Workspace):** {client_name} (workspace_id: {wid})\n"
            else:
                result += f"**Workspace ID:** {wid}\n"
        result += f"**Requester ID:** {ticket.get('requester_id', 'N/A')}\n"
        result += f"**Agent ID:** {ticket.get('responder_id', 'N/A')}\n"
        result += f"**Created:** {ticket.get('created_at', 'N/A')}\n"
        result += f"**Updated:** {ticket.get('updated_at', 'N/A')}\n"
        result += f"**Due By:** {ticket.get('due_by', 'N/A')}\n\n"

        description = ticket.get("description_text", ticket.get("description", ""))
        if description:
            description = re.sub("<[^<]+?>", "", description)
            result += f"**Description:**\n{description[:500]}\n"

        return result

    def _format_conversations(
        self, conversations: list, include_private: bool = True
    ) -> str:
        """Format conversations/notes for display"""
        result = "---\n## 💬 **Conversations & Notes**\n\n"

        sorted_convos = sorted(conversations, key=lambda x: x.get("created_at", ""))

        count = 0
        for convo in sorted_convos:
            is_private = convo.get("private", False)

            if is_private and not include_private:
                continue

            count += 1

            if is_private:
                convo_type = "🔒 **Private Note**"
            else:
                incoming = convo.get("incoming", False)
                if incoming:
                    convo_type = "📥 **Customer Reply**"
                else:
                    convo_type = "📤 **Agent Reply**"

            body = convo.get("body_text", convo.get("body", ""))
            if body:
                body = re.sub("<[^<]+?>", "", body)
                body = body.strip()[:1000]

            created_at = convo.get("created_at", "N/A")
            if created_at and created_at != "N/A":
                created_at = created_at[:19].replace("T", " ")

            user_id = convo.get("user_id", "Unknown")

            result += f"### {convo_type}\n"
            result += f"**From:** User/Agent ID {user_id} | **Date:** {created_at}\n"
            result += f"```\n{body}\n```\n\n"

        if count == 0:
            result += "No conversations found matching your criteria.\n"
        else:
            result += f"---\n**Total: {count} conversation(s)/note(s)**\n"

        return result

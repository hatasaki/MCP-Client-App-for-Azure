"""Azure OpenAI *Responses API* helper class.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable
from openai import AsyncAzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from datetime import datetime
import os
import json
import asyncio

from .config import DEFAULT_SYSTEM_PROMPT

__all__ = ["AzureOpenAIResponseService"]


class AzureOpenAIResponseService:
    """Wrapper around the *completions* (text) endpoint.

    Parameters are intentionally very similar to ``AzureOpenAIService`` so that
    the server can switch between the two implementations transparently based
    on configuration.
    """

    def __init__(self, config: Dict[str, Any]):
        # Common configuration keys with ChatCompletion service
        self.config = config
        # normalize key lookups for auth/env as well
        def cfg_get(*keys: str, default: Any = None):
            for k in keys:
                if k in config:
                    return config.get(k)
            return default
        key = cfg_get("api_key", "apiKey")
        deployment = cfg_get("deployment")
        if key:
            self.client = AsyncAzureOpenAI(
                azure_endpoint=cfg_get("endpoint"),
                api_key=key,
                api_version=cfg_get("api_version", "apiVersion"),
                azure_deployment=deployment,
            )
        else:
            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(exclude_interactive_browser_credential=False),
                "https://cognitiveservices.azure.com/.default",
            )
            self.client = AsyncAzureOpenAI(
                azure_endpoint=cfg_get("endpoint"),
                azure_ad_token_provider=token_provider,
                api_version=cfg_get("api_version", "apiVersion"),
                azure_deployment=deployment,
            )
        self.deployment = deployment

    # -------------------------------------------------------------------------------------
    # Public helpers – signature kept intentionally close to ChatCompletion version
    # -------------------------------------------------------------------------------------
    async def send_message(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        tool_executor: Optional[Callable[[str, str, Dict[str, Any]], Any]] = None,
        approval_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        auto_approve: bool = False,
        previous_response_id: Optional[str] = None,
        forced_tool_name: Optional[str] = None,
        should_stop: Optional[Callable[[str], bool]] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        """Generate a response with the *text* completion endpoint.

        The Responses API cannot call functions.  If *tools* is supplied a
        ``ValueError`` will be raised so that the caller can fall back to
        ChatCompletion.
        """
        # Combine history into a single prompt: ChatCompletion style histories are
        # flattened into a simple text conversation for the legacy endpoint.
        # Format:   <role>: <message>\n\n  ...
        # Assistant will respond after the conversation context.
        conv_lines: list[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            conv_lines.append(f"{role}: {content}")
        prompt = "\n\n".join(conv_lines)

        # Ensure a system prompt exists
        if not messages or messages[0].get("role") != "system":
            date_str = datetime.now().strftime("%B %d, %Y")
            base_prompt = self.config.get("system_prompt", DEFAULT_SYSTEM_PROMPT).rstrip()
            system_prompt = f"{base_prompt}\nCurrent date: {date_str}"
            prompt = f"system: {system_prompt}\n\n" + prompt

        # Helper to prefer explicitly provided keys, supporting camelCase / snake_case
        def cfg_get(*keys: str, default: Any = None):
            for k in keys:
                if k in self.config:
                    return self.config.get(k)
            return default

        # Optional generation parameters – map to Responses API naming
        temp = cfg_get("temperature")
        top_p = cfg_get("top_p", "topP")
        max_out = cfg_get("max_output_tokens", "max_tokens", "maxTokens")
        # New reasoning params for GPT-5 Responses API
        reasoning_effort = (cfg_get("reasoning_effort", "reasoningEffort", default="") or "").strip().lower()
        verbosity = (cfg_get("verbosity", default="") or "").strip().lower()
        # max_completion_tokens = cfg_get("max_completion_tokens", "maxCompletionTokens")

        # convert tool definitions to Responses API format (flattened)
        converted_tools: list[dict] = []
        if tools:
            for t in tools:
                if not t:
                    continue
                name = None
                desc = ""
                params = {"type": "object", "properties": {}}
                if isinstance(t, dict) and t.get("type") == "function" and isinstance(t.get("function"), dict):
                    fn = t["function"]
                    name = fn.get("name") or t.get("name")
                    desc = fn.get("description") or t.get("description", "")
                    params = (
                        fn.get("parameters")
                        or t.get("parameters")
                        or fn.get("input_schema")
                        or t.get("input_schema")
                        or params
                    )
                elif isinstance(t, dict):
                    name = t.get("name")
                    desc = t.get("description", "")
                    params = t.get("parameters") or t.get("input_schema") or params
                if not name:
                    continue  # skip invalid tool defs
                # Responses API expects flattened function tool schema
                converted_tools.append({
                    "type": "function",
                    "name": name,
                    "description": desc,
                    "parameters": params,
                })

        # helper to build params per request
        def build_params(inp: Any, prev_resp_id: str | None = None, forced_tool: str | None = None):
            p = {
                "model": self.deployment,
                "input": inp,
            }
            if prev_resp_id:
                p["previous_response_id"] = prev_resp_id
            # optional gens
            if temp not in (None, ""):
                p["temperature"] = temp
            if top_p not in (None, ""):
                p["top_p"] = top_p
            if max_out not in (None, ""):
                p["max_output_tokens"] = max_out
            # GPT-5 Responses API nested params
            if reasoning_effort and reasoning_effort != "none":
                p["reasoning"] = {"effort": reasoning_effort}
            if verbosity and verbosity != "none":
                p.setdefault("text", {})["verbosity"] = verbosity
            # Advertise tools only on the very first request to avoid infinite recursion
            if converted_tools and not prev_resp_id:
                p["tools"] = converted_tools
                p["tool_choice"] = {"type": "function", "name": forced_tool} if forced_tool else "auto"
            return p

        executed: list[str] = []
        prev_resp_id: str | None = previous_response_id or None
        # forced_tool_name carried from args
        while True:
            # Early cancellation check before spending tokens
            if should_stop and should_stop(session_id):
                return {"stopped": True}
            params = build_params(prompt, prev_resp_id, forced_tool_name)
            print("[ResponsesAPI] create", params)
            resp = await self.client.responses.create(**params)
            msg_output = getattr(resp, "output", [])
            # check tool call
            tool_called = False
            call_outputs: list[dict] = []
            for item in msg_output:
                if getattr(item, "type", "") == "function_call":
                    tool_called = True
                    name = getattr(item, "name", "")
                    args_json = getattr(item, "arguments", "{}")
                    call_id = getattr(item, "call_id", None) or getattr(item, "id", None)
                    try:
                        args = json.loads(args_json) if isinstance(args_json, str) else args_json
                    except Exception:
                        args = {}
                    print(f"➡️ tool {name} id {call_id} args {args}")
                    # Ask for approval unless auto-approved
                    if not auto_approve and approval_callback:
                        try:
                            approved, always = await asyncio.wait_for(
                                approval_callback({"name": name, "arguments": args}), timeout=120
                            )
                        except asyncio.TimeoutError:
                            print(f"[ResponsesAPI] approval timeout for tool {name} ({call_id})")
                            approved, always = False, False
                        if always:
                            auto_approve = True
                        if not approved:
                            call_outputs.append({
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": "[Denied by user]",
                            })
                            continue  # skip execution
                    if should_stop and should_stop(session_id):
                        return {"stopped": True}
                    if tool_executor:
                        result = await tool_executor(session_id, name, args)
                    else:
                        result = ""
                    executed.append(name)
                    # Build output as string; JSON-encode dict/list results
                    if isinstance(result, (dict, list)):
                        output_str = json.dumps(result, ensure_ascii=False)
                    else:
                        output_str = str(result) if result is not None else ""
                    # Log tool output similar to ChatCompletion implementation
                    print(f"⬅️  Result from tool '{name}': {output_str}")
                    call_outputs.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output_str,
                    })
            if tool_called:
                prev_resp_id = resp.id
                forced_tool_name = None
                prompt = call_outputs
                print(f"[ResponsesAPI] follow-up with {len(call_outputs)} function_call_output(s)")
                continue  # next loop iteration
            # finished
            text = getattr(resp, "output_text", None)
            if text is None and msg_output:
                text = "".join([getattr(i, "text", "") for i in msg_output if hasattr(i, "text")])
            return {"content": text or "", "response_id": resp.id, "toolCalls": executed}

from typing import Any, Dict, List, Optional, Callable
from openai import AsyncAzureOpenAI  # new client
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
import os
import json
from datetime import datetime

from .config import DEFAULT_SYSTEM_PROMPT

class AzureOpenAIService:
    def __init__(self, config: Dict[str, Any]):
        # configure OpenAI SDK for Azure
        self.config=config
        key=config.get("api_key")
        deployment=config.get("deployment")
        if key:
            self.client=AsyncAzureOpenAI(
                azure_endpoint=config.get("endpoint"),
                api_key=key,
                api_version=config.get("api_version"),
                azure_deployment=deployment,
            )
        else:
            token_provider = get_bearer_token_provider(DefaultAzureCredential(exclude_interactive_browser_credential=False), "https://cognitiveservices.azure.com/.default")

            self.client=AsyncAzureOpenAI(
                azure_endpoint=config.get("endpoint"),
                azure_ad_token_provider=token_provider,
                api_version=config.get("api_version"),
                azure_deployment=deployment,
            )
        self.deployment=deployment

    async def send_message(self,
        session_id: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_executor: Callable[[str, str, Dict[str, Any]], Any],
        approval_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        auto_approve: bool = False,
        previous_response_id: Optional[str] = None,
        forced_tool_name: Optional[str] = None,  # if specified, force the model to call this tool
        should_stop: Optional[Callable[[str], bool]] = None  # callback to check if user requested stop
    ) -> Dict[str, Any]:
        """ChatCompletion helper with MCP tool support.
        If *should_stop* returns True for the given session_id at any point, tool execution and
        further requests are aborted immediately.
        """
        # ensure system prompt positioned first
        if not messages or messages[0].get("role") != "system":
            date_str = datetime.now().strftime("%B %d, %Y")
            base_prompt = self.config.get("system_prompt", DEFAULT_SYSTEM_PROMPT).rstrip()
            system_prompt = f"{base_prompt}\nCurrent date: {date_str}"
            messages = [{"role": "system", "content": system_prompt}] + messages
        # iterative loop like CLI chat
        executed=[]
        while True:
            # Early cancellation check before spending tokens
            if should_stop and should_stop(session_id):
                return {"stopped": True}
            params={"model":self.deployment,"messages":messages}
            # Optional generation parameters
            temp=self.config.get("temperature")
            if temp is not None:
                params["temperature"]=temp
            top_p=self.config.get("top_p")
            if top_p is not None:
                params["top_p"]=top_p
            max_toks=self.config.get("max_tokens")
            if max_toks:
                params["max_tokens"]=max_toks
            if tools:
                params["functions"] = tools
                # Force a particular tool call when requested, otherwise allow the model to pick automatically
                if forced_tool_name:
                    params["function_call"] = {"name": forced_tool_name}
                else:
                    params["function_call"] = "auto"
            resp=await self.client.chat.completions.create(**params)
            msg=resp.choices[0].message
            if msg.function_call:
                name = msg.function_call.name
                args = json.loads(msg.function_call.arguments or "{}")
                # auto approve always in gui for now
                if not auto_approve and approval_callback:
                    approved, always = await approval_callback({"name": name, "arguments": args})
                    if always:
                        auto_approve = True
                    if not approved:
                        messages.append({"role": "assistant", "content": "[Tool execution skipped]"})
                        continue
                result = await tool_executor(session_id, name, args)
                # serialize tool result safely
                if isinstance(result, (str, int, float, bool)):
                    rtxt = str(result)
                else:
                    try:
                        rtxt = json.dumps(result, ensure_ascii=False, default=lambda o: getattr(o, '__dict__', str(o)))
                    except Exception:
                        rtxt = str(result)
                messages.append({"role": "function", "name": name, "content": rtxt})
                executed.append(name)
                continue
            return {"content":msg.content or "", "response_id":resp.id, "toolCalls": executed}

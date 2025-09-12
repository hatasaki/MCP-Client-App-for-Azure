export interface MCPServerConfig {
  id?: string;
  name: string;
  transport: 'stdio' | 'sse' | 'http';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  cwd?: string;
}

export interface MCPTool {
  name: string;
  description: string;
  parameters: any;
  serverId: string;
  serverName?: string; // 追加
}

export interface ChatMessage {
  id: string;
  content: string;
  role: 'user' | 'assistant';
  timestamp: Date;
  tools?: any[];
  toolCalls?: Array<{ 
    name: string; 
    parameters: any; 
    output?: any;
    error?: string;
  }>;
}

export interface ChatSession {
  id: string;
  name: string;
  messages: ChatMessage[];
  createdAt: Date;
  updatedAt: Date;
  autoApproveAll?: boolean;
  responseId?: string; // Azure OpenAI Responses API用のID
}

export interface AzureOpenAIConfig {
  endpoint: string;
  apiKey: string;
  deployment: string;
  apiVersion: string;
}

// Alias for backward compatibility
export type AzureConfig = AzureOpenAIConfig & {
  apiKey?: string;
  apiVersion?: string;
  system_prompt?: string;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  systemPrompt?: string;
  topP?: number;
  maxTokens?: number;
  apiType?: 'chat' | 'responses';
  // New Responses API (GPT-5) parameters (UI friendly names)
  reasoningEffort?: 'none' | 'minimal' | 'low' | 'medium' | 'high';
  verbosity?: 'none' | 'low' | 'medium' | 'high';
  maxCompletionTokens?: number;
  // Backend compatibility keys (when reading from server)
  reasoning_effort?: string;
  max_completion_tokens?: number | string;
};

export interface SelectedTool {
  id: string;
  name: string;
  description: any;
  parameters: any;
  serverId: string;
  serverName?: string;
}

export interface ApprovalRequest {
  id: string;
  toolName: string;
  name: string;
  arguments: any;
  parameters: any;
  description: string;
  serverId: string;
  server_label: string;
}

export interface ToolApprovalRequest {
  id: string;
  toolName: string;
  parameters: any;
  description: string;
  serverId: string;
}


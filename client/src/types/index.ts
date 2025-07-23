export interface MCPServerConfig {
  id?: string;
  name: string;
  transport: 'stdio' | 'sse' | 'http';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
}

export interface MCPTool {
  name: string;
  description: string;
  parameters: any;
  serverId: string;
  serverName?: string; // 追加
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  toolCalls?: any[];
}

export interface ChatSession {
  id: string;
  name: string;
  messages: ChatMessage[];
  createdAt: Date;
  updatedAt: Date;
  autoApproveAll: boolean;
}

export interface AzureConfig {
  endpoint: string;
  apiKey?: string;
  deployment: string;
  apiVersion?: string;
  system_prompt?: string;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  // camelCase aliases for UI
  systemPrompt?: string;
  topP?: number;
  maxTokens?: number;
}

export interface ApprovalRequest {
  id: string;
  arguments: any;
  name: string;
  server_label: string;
}

export interface SelectedTool {
  serverId: string;
  name: string;
  description: string;
  parameters: any;
}

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
    serverName?: string;
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
}
export interface AzureOpenAIConfig {
    endpoint: string;
    apiKey: string;
    deployment: string;
    apiVersion: string;
}
export type AzureConfig = AzureOpenAIConfig;
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
//# sourceMappingURL=types.d.ts.map
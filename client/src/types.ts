export type EndpointKind = 'project' | 'model';
export type ApiType = 'responses' | 'chat_completions' | 'claude_messages';
export type VersionMode = 'v1' | 'dated' | 'provider';
export type AuthType = 'entra_id' | 'api_key';
export type SecretAction = 'keep' | 'set' | 'clear';
export type MessageStatus = 'streaming' | 'completed' | 'cancelled' | 'interrupted' | 'error';

export type AttachmentMediaType = 'application/pdf' | 'text/plain' | 'image/jpeg' | 'image/png';

export interface ChatAttachment {
  id: string;
  filename: string;
  mediaType: AttachmentMediaType;
  sizeBytes: number;
  contentHash: string;
}

export interface ChatAttachmentUpload {
  name: string;
  mediaType: AttachmentMediaType;
  size: number;
  data: ArrayBuffer;
}

export interface MCPServerConfig {
  id?: string;
  name: string;
  transport: 'http' | 'stdio' | 'sse';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  cwd?: string;
}

export interface MCPTool {
  id: string;
  qualifiedId: string;
  name: string;
  originalName: string;
  displayName: string;
  description: string;
  parameters: Record<string, unknown>;
  serverId: string;
  serverName: string;
}

export interface ChatMessage {
  id: string;
  content: string;
  role: 'user' | 'assistant';
  timestamp: string | Date;
  status?: MessageStatus;
  toolCalls?: string[];
  attachments?: ChatAttachment[];
}

export interface ChatSession {
  schemaVersion?: number;
  id: string;
  name: string;
  messages: ChatMessage[];
  createdAt: string | Date;
  updatedAt: string | Date;
  autoApproveAll?: boolean;
  stateEpoch?: number;
  selectedModel?: ModelSelection | null;
}

export interface ResponsesOptions {
  temperature?: number;
  topP?: number;
  maxOutputTokens?: number;
  reasoningEffort?: 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
  reasoningSummary?: 'auto' | 'concise' | 'detailed';
  verbosity?: 'low' | 'medium' | 'high';
  store?: boolean;
  parallelToolCalls?: boolean;
  serviceTier?: 'auto' | 'default' | 'flex' | 'priority';
  truncation?: 'auto' | 'disabled';
  maxToolCalls?: number;
  safetyIdentifier?: string;
  promptCacheKey?: string;
  metadata?: Record<string, string>;
}

export interface ChatCompletionsOptions {
  temperature?: number;
  topP?: number;
  maxCompletionTokens?: number;
  reasoningEffort?: 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
  verbosity?: 'low' | 'medium' | 'high';
  stop?: string | string[];
  seed?: number;
  frequencyPenalty?: number;
  presencePenalty?: number;
  logprobs?: boolean;
  topLogprobs?: number;
  store?: boolean;
  parallelToolCalls?: boolean;
  serviceTier?: 'auto' | 'default' | 'flex' | 'priority';
  safetyIdentifier?: string;
  promptCacheKey?: string;
  metadata?: Record<string, string>;
}

export type ClaudeThinking =
  | { type: 'disabled' }
  | { type: 'enabled'; budgetTokens: number }
  | { type: 'adaptive' };

export interface ClaudeMessagesOptions {
  maxTokens: number;
  temperature?: number;
  topP?: number;
  topK?: number;
  stopSequences?: string[];
  thinking?: ClaudeThinking;
  effort?: 'low' | 'medium' | 'high' | 'max';
  serviceTier?: 'auto' | 'standard_only';
  parallelToolUse?: boolean;
  metadataUserId?: string;
}

export type FoundryOptions = ResponsesOptions | ChatCompletionsOptions | ClaudeMessagesOptions;

export interface ModelSelection {
  apiType: ApiType;
  model: string;
}

interface ApiProfileBase {
  models: string[];
  versionMode: VersionMode;
  apiVersion?: string;
}

export interface ResponsesProfile extends ApiProfileBase {
  apiType: 'responses';
  options: ResponsesOptions;
}

export interface ChatCompletionsProfile extends ApiProfileBase {
  apiType: 'chat_completions';
  options: ChatCompletionsOptions;
}

export interface ClaudeMessagesProfile extends ApiProfileBase {
  apiType: 'claude_messages';
  options: ClaudeMessagesOptions;
}

export type ApiProfile = ResponsesProfile | ChatCompletionsProfile | ClaudeMessagesProfile;

export interface FoundrySettings {
  schemaVersion: 4;
  endpointKind: EndpointKind;
  endpoint: string;
  auth: {
    type: AuthType;
    apiKeyConfigured: boolean;
    apiKeyNeedsReplacement?: boolean;
  };
  agentInstructions: string;
  apiProfiles: ApiProfile[];
  defaultSelection: ModelSelection;
}

export interface FoundrySettingsWrite {
  schemaVersion: 4;
  endpointKind: EndpointKind;
  endpoint: string;
  auth: {
    type: AuthType;
    apiKey: {
      action: SecretAction;
      value?: string;
    };
  };
  agentInstructions: string;
  apiProfiles: ApiProfile[];
  defaultSelection: ModelSelection;
}

export interface SelectedTool {
  id: string;
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  serverId: string;
  serverName: string;
}

export interface ChatEventBase {
  requestId: string;
  sessionId: string;
  messageId: string;
  epoch: number;
  sequence: number;
}

export interface ChatStartedEvent extends ChatEventBase {
  userMessageId: string;
  stateReset: boolean;
  modelSelection: ModelSelection;
}

export interface ChatDeltaEvent extends ChatEventBase {
  delta: string;
}

export interface ChatToolStatusEvent extends ChatEventBase {
  toolId?: string;
  toolName?: string;
  callId?: string;
  status: 'requested' | 'completed' | 'error';
  arguments?: unknown;
  error?: string;
}

export interface ApprovalRequestItem {
  id: string;
  name: string;
  arguments: unknown;
  serverLabel?: string;
}

export interface ChatApprovalRequiredEvent extends ChatEventBase {
  requests: ApprovalRequestItem[];
}

export interface ChatTerminalEvent extends ChatEventBase {
  content: string;
  toolCalls?: string[];
  session?: ChatSession;
  code?: string;
  message?: string;
}

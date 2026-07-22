import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Avatar,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  IconButton,
  List,
  ListItem,
  MenuItem,
  Paper,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import BuildIcon from '@mui/icons-material/Build';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined';
import ImageOutlinedIcon from '@mui/icons-material/ImageOutlined';
import PersonIcon from '@mui/icons-material/Person';
import AddIcon from '@mui/icons-material/Add';
import SendIcon from '@mui/icons-material/Send';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import StopIcon from '@mui/icons-material/Stop';
import { Socket } from 'socket.io-client';

import MarkdownRenderer from './MarkdownRenderer';
import {
  ChatApprovalRequiredEvent,
  ChatDeltaEvent,
  ChatMessage,
  ChatSession,
  ChatAttachmentUpload,
  AttachmentMediaType,
  ChatStartedEvent,
  ChatTerminalEvent,
  ChatToolStatusEvent,
  FoundrySettings,
  MCPTool,
  ModelSelection,
  SelectedTool,
} from '../types';

interface ChatInterfaceProps {
  session: ChatSession;
  availableTools: MCPTool[];
  settingsConfigured: boolean;
  settings: FoundrySettings;
  socket: Socket | null;
}

interface LiveMessage {
  messageId: string;
  content: string;
}

const API_LABELS: Record<ModelSelection['apiType'], string> = {
  responses: 'Responses',
  chat_completions: 'Chat Completions',
  claude_messages: 'Claude Messages',
};

const MAX_ATTACHMENT_COUNT = 10;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024;
const SUPPORTED_MEDIA_TYPES = new Set<AttachmentMediaType>([
  'application/pdf',
  'text/plain',
  'image/jpeg',
  'image/png',
]);

const formatFileSize = (size: number) => size >= 1024 * 1024
  ? `${(size / (1024 * 1024)).toFixed(1)} MB`
  : `${Math.max(1, Math.round(size / 1024))} KB`;

const makeRequestId = () =>
  typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

const ChatInterface: React.FC<ChatInterfaceProps> = ({
  session,
  availableTools,
  settingsConfigured,
  settings,
  socket,
}) => {
  const [message, setMessage] = useState('');
  const [attachments, setAttachments] = useState<ChatAttachmentUpload[]>([]);
  const [selectedTools, setSelectedTools] = useState<SelectedTool[]>([]);
  const [showToolSelector, setShowToolSelector] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [liveMessage, setLiveMessage] = useState<LiveMessage | null>(null);
  const [toolStatuses, setToolStatuses] = useState<ChatToolStatusEvent[]>([]);
  const [approvalBatch, setApprovalBatch] = useState<ChatApprovalRequiredEvent | null>(null);
  const [approvalDecisions, setApprovalDecisions] = useState<Record<string, boolean>>({});
  const availableModels = useMemo(() => settings.apiProfiles.flatMap((profile) =>
    profile.models.map((model) => ({ apiType: profile.apiType, model }))
  ), [settings.apiProfiles]);
  const modelKey = useCallback((selection: ModelSelection) =>
    JSON.stringify([selection.apiType, selection.model]), []);
  const validSelection = useCallback((selection?: ModelSelection | null) =>
    !!selection && availableModels.some((item) => modelKey(item) === modelKey(selection)),
  [availableModels, modelKey]);
  const [selectedModel, setSelectedModel] = useState<ModelSelection>(() =>
    validSelection(session.selectedModel) ? session.selectedModel! : settings.defaultSelection
  );
  const activeRequestRef = useRef<string | null>(null);
  const lastSequenceRef = useRef(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!socket) return;

    const accepts = (
      event: { sessionId?: string; requestId?: string; sequence?: number },
      terminal = false,
    ) => {
      if (event.sessionId && event.sessionId !== session.id) return false;
      if (event.requestId && event.requestId !== activeRequestRef.current) return false;
      if (event.sequence !== undefined) {
        if (!terminal && event.sequence <= lastSequenceRef.current) return false;
        lastSequenceRef.current = Math.max(lastSequenceRef.current, event.sequence);
      }
      return true;
    };

    const started = (event: ChatStartedEvent) => {
      if (!accepts(event)) return;
      activeRequestRef.current = event.requestId;
      setLiveMessage({ messageId: event.messageId, content: '' });
      setIsLoading(true);
      if (event.stateReset) {
        setErrorMessage('Agent state was rebuilt. Completed text history was replayed under the current settings.');
      }
      setSelectedModel(event.modelSelection);
    };
    const delta = (event: ChatDeltaEvent) => {
      if (!accepts(event)) return;
      setLiveMessage((current) => ({
        messageId: event.messageId,
        content: `${current?.messageId === event.messageId ? current.content : ''}${event.delta}`,
      }));
    };
    const toolStatus = (event: ChatToolStatusEvent) => {
      if (!accepts(event)) return;
      setToolStatuses((current) => {
        const key = event.callId || event.toolId || `${event.sequence}`;
        const index = current.findIndex((item) => (item.callId || item.toolId || `${item.sequence}`) === key);
        if (index < 0) return [...current, event];
        return current.map((item, itemIndex) => itemIndex === index ? { ...item, ...event } : item);
      });
    };
    const approval = (event: ChatApprovalRequiredEvent) => {
      if (!accepts(event)) return;
      setApprovalBatch(event);
      setApprovalDecisions(Object.fromEntries(event.requests.map((request) => [request.id, false])));
    };
    const terminal = (event: ChatTerminalEvent) => {
      if (!accepts(event, true)) return;
      setLiveMessage({ messageId: event.messageId, content: event.content || '' });
      setIsLoading(false);
      setApprovalBatch(null);
      setToolStatuses([]);
      if (event.message) setErrorMessage(event.message);
      activeRequestRef.current = null;
    };
    const disconnected = () => {
      activeRequestRef.current = null;
      lastSequenceRef.current = 0;
      setIsLoading(false);
      setApprovalBatch(null);
      setToolStatuses([]);
      setLiveMessage(null);
      setErrorMessage('Connection was interrupted. The server cancelled any active run.');
    };

    socket.on('chat:started', started);
    socket.on('chat:delta', delta);
    socket.on('chat:tool-status', toolStatus);
    socket.on('chat:approval-required', approval);
    socket.on('chat:completed', terminal);
    socket.on('chat:cancelled', terminal);
    socket.on('chat:error', terminal);
    socket.on('disconnect', disconnected);
    return () => {
      socket.off('chat:started', started);
      socket.off('chat:delta', delta);
      socket.off('chat:tool-status', toolStatus);
      socket.off('chat:approval-required', approval);
      socket.off('chat:completed', terminal);
      socket.off('chat:cancelled', terminal);
      socket.off('chat:error', terminal);
      socket.off('disconnect', disconnected);
    };
  }, [socket, session.id]);

  useEffect(() => {
    const persisted = liveMessage
      ? session.messages.find((item) => item.id === liveMessage.messageId)
      : undefined;
    if (persisted && persisted.status !== 'streaming' && persisted.content === liveMessage?.content) {
      setLiveMessage(null);
    }
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [session.messages, liveMessage]);

  useEffect(() => {
    activeRequestRef.current = null;
    lastSequenceRef.current = 0;
    setIsLoading(false);
    setLiveMessage(null);
    setToolStatuses([]);
    setApprovalBatch(null);
    setApprovalDecisions({});
    setAttachments([]);
  }, [session.id]);

  useEffect(() => {
    setSelectedModel(validSelection(session.selectedModel) ? session.selectedModel! : settings.defaultSelection);
  }, [session.id, session.selectedModel, settings.defaultSelection, validSelection]);

  useEffect(() => {
    setSelectedTools((current) => current.filter((selected) =>
      availableTools.some((tool) => tool.qualifiedId === selected.id)
    ));
  }, [availableTools]);

  const displayedMessages = useMemo(() => session.messages.map((item) =>
    liveMessage?.messageId === item.id ? { ...item, content: liveMessage.content } : item
  ), [session.messages, liveMessage]);

  const groupedTools = useMemo(() => availableTools.reduce((groups, tool) => {
    (groups[tool.serverId] ||= []).push(tool);
    return groups;
  }, {} as Record<string, MCPTool[]>), [availableTools]);

  const sendMessage = () => {
    if (!socket || !settingsConfigured || (!message.trim() && !attachments.length) || isLoading) return;
    const requestId = makeRequestId();
    activeRequestRef.current = requestId;
    lastSequenceRef.current = 0;
    setIsLoading(true);
    setErrorMessage(null);
    setLiveMessage(null);
    setToolStatuses([]);
    socket.emit('chat:send', {
      requestId,
      sessionId: session.id,
      message: message.trim(),
      attachments,
      selectedToolIds: selectedTools.map((tool) => tool.id),
      selectedModel,
    });
    setMessage('');
    setAttachments([]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const addAttachments = async (files: FileList | null) => {
    if (!files?.length) return;
    const incoming = Array.from(files);
    const nextCount = attachments.length + incoming.length;
    if (nextCount > MAX_ATTACHMENT_COUNT) {
      setErrorMessage(`A message can contain at most ${MAX_ATTACHMENT_COUNT} attachments.`);
      return;
    }
    const accepted: ChatAttachmentUpload[] = [];
    let totalSize = attachments.reduce((sum, attachment) => sum + attachment.size, 0);
    for (const file of incoming) {
      const mediaType = (file.type === 'image/jpg' ? 'image/jpeg' : file.type) as AttachmentMediaType;
      if (!SUPPORTED_MEDIA_TYPES.has(mediaType)) {
        setErrorMessage(`${file.name} is not supported. Choose PDF, TXT, JPEG/JPG, or PNG.`);
        return;
      }
      if (!file.size || file.size > MAX_ATTACHMENT_BYTES) {
        setErrorMessage(`${file.name} must be larger than 0 bytes and no larger than 10 MB.`);
        return;
      }
      totalSize += file.size;
      if (totalSize > MAX_TOTAL_ATTACHMENT_BYTES) {
        setErrorMessage('Attachments exceed the 25 MB combined request limit.');
        return;
      }
      accepted.push({
        name: file.name,
        mediaType,
        size: file.size,
        data: await file.arrayBuffer(),
      });
    }
    setAttachments((current) => [...current, ...accepted]);
    setErrorMessage(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const cancel = () => {
    if (!socket || !activeRequestRef.current) return;
    socket.emit('chat:cancel', {
      requestId: activeRequestRef.current,
      sessionId: session.id,
    });
  };

  const resolveApproval = (mode: 'submit' | 'deny' | 'always') => {
    if (!socket || !approvalBatch) return;
    const decisions = approvalBatch.requests.map((request) => ({
      requestId: request.id,
      approved: mode === 'always' || (mode === 'submit' && !!approvalDecisions[request.id]),
    }));
    socket.emit('chat:approval-resolve', {
      requestId: approvalBatch.requestId,
      sessionId: session.id,
      decisions,
      approveAll: mode === 'always',
    });
    setApprovalBatch(null);
  };

  const toggleTool = (tool: MCPTool, selected: boolean) => {
    setSelectedTools((current) => selected
      ? [...current, {
          id: tool.qualifiedId,
          name: tool.name,
          description: tool.description,
          parameters: tool.parameters,
          serverId: tool.serverId,
          serverName: tool.serverName,
        }]
      : current.filter((item) => item.id !== tool.qualifiedId)
    );
  };

  const toggleServer = (serverId: string, tools: MCPTool[]) => {
    const allSelected = tools.every((tool) => selectedTools.some((selected) => selected.id === tool.qualifiedId));
    setSelectedTools((current) => allSelected
      ? current.filter((item) => item.serverId !== serverId)
      : [
          ...current.filter((item) => item.serverId !== serverId),
          ...tools.map((tool) => ({
            id: tool.qualifiedId,
            name: tool.name,
            description: tool.description,
            parameters: tool.parameters,
            serverId: tool.serverId,
            serverName: tool.serverName,
          })),
        ]
    );
  };

  const formatTimestamp = (timestamp: string | Date) => new Date(timestamp).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6">{session.name}</Typography>
        <Button
          variant="outlined"
          startIcon={<BuildIcon />}
          onClick={() => setShowToolSelector(true)}
          size="small"
          sx={{ mt: 1 }}
          disabled={!settingsConfigured}
        >
          Select tools ({selectedTools.length}/{availableTools.length})
        </Button>
        {session.autoApproveAll && <Alert severity="warning" sx={{ mt: 1 }}>This session automatically approves tool calls.</Alert>}
        {!availableTools.length && <Alert severity="info" sx={{ mt: 1 }}>No MCP tools are currently available.</Alert>}
        {errorMessage && <Alert severity="info" onClose={() => setErrorMessage(null)} sx={{ mt: 1 }}>{errorMessage}</Alert>}
      </Box>

      <Box sx={{ flexGrow: 1, overflow: 'auto', p: 2 }}>
        <List>
          {displayedMessages.map((item: ChatMessage) => (
            <ListItem key={item.id} sx={{ justifyContent: item.role === 'user' ? 'flex-end' : 'flex-start', mb: 1 }}>
              <Box sx={{ display: 'flex', flexDirection: item.role === 'user' ? 'row-reverse' : 'row', width: '100%', alignItems: 'flex-start' }}>
                <Avatar sx={{ bgcolor: item.role === 'user' ? 'primary.main' : 'secondary.main', mx: 1 }}>
                  {item.role === 'user' ? <PersonIcon /> : <SmartToyIcon />}
                </Avatar>
                <Paper sx={{ p: 2, maxWidth: 'calc(100% - 56px)', bgcolor: item.role === 'user' ? 'primary.main' : 'grey.100', color: item.role === 'user' ? 'white' : 'inherit', overflowWrap: 'anywhere', userSelect: 'text' }}>
                  {!!item.attachments?.length && (
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75, mb: item.content ? 1 : 0 }}>
                      {item.attachments.map((attachment) => (
                        <Chip
                          key={attachment.id}
                          size="small"
                          color={item.role === 'user' ? 'default' : undefined}
                          icon={attachment.mediaType.startsWith('image/') ? <ImageOutlinedIcon /> : <DescriptionOutlinedIcon />}
                          label={`${attachment.filename} · ${formatFileSize(attachment.sizeBytes)}`}
                          sx={{ maxWidth: '100%', bgcolor: item.role === 'user' ? 'rgba(255,255,255,0.9)' : undefined }}
                        />
                      ))}
                    </Box>
                  )}
                  {item.content ? <MarkdownRenderer content={item.content} color={item.role === 'user' ? 'white' : 'inherit'} /> : item.status === 'streaming' ? <CircularProgress size={18} /> : null}
                  {!!item.toolCalls?.length && <Typography variant="caption" display="block">Tools: {item.toolCalls.join(', ')}</Typography>}
                  <Typography variant="caption" display="block" sx={{ opacity: 0.7, mt: 1 }}>
                    {formatTimestamp(item.timestamp)}{item.status && item.status !== 'completed' ? ` · ${item.status}` : ''}
                  </Typography>
                </Paper>
              </Box>
            </ListItem>
          ))}
          {toolStatuses.map((status) => (
            <ListItem key={status.callId || status.toolId || status.sequence} sx={{ justifyContent: 'center' }}>
              <Typography variant="body2" color={status.status === 'error' ? 'error' : 'text.secondary'}>
                {status.toolName || status.toolId || 'Tool'}: {status.status}
              </Typography>
            </ListItem>
          ))}
          <div ref={messagesEndRef} />
        </List>
      </Box>

      <Paper sx={{ p: 2, mt: 'auto', bgcolor: 'background.default' }}>
        {!!attachments.length && (
          <Box aria-label="Selected attachments" sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75, mb: 1.25 }}>
            {attachments.map((attachment, index) => (
              <Chip
                key={`${attachment.name}-${attachment.size}-${index}`}
                icon={attachment.mediaType.startsWith('image/') ? <ImageOutlinedIcon /> : <DescriptionOutlinedIcon />}
                label={`${attachment.name} · ${formatFileSize(attachment.size)}`}
                onDelete={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                disabled={isLoading}
                sx={{ maxWidth: '100%' }}
              />
            ))}
          </Box>
        )}
        <Box data-testid="message-input-row" sx={{ display: 'flex', alignItems: 'center' }}>
          <input
            ref={fileInputRef}
            type="file"
            hidden
            multiple
            accept=".pdf,.txt,.jpg,.jpeg,.png,application/pdf,text/plain,image/jpeg,image/png"
            onChange={(event) => void addAttachments(event.target.files)}
            aria-label="Attachment file input"
          />
          <Tooltip title="Attach PDF, TXT, JPEG, or PNG (10 MB each; 25 MB total)">
            <span>
              <IconButton
                aria-label="Add attachments"
                color="primary"
                onClick={() => fileInputRef.current?.click()}
                disabled={!settingsConfigured || isLoading || attachments.length >= MAX_ATTACHMENT_COUNT}
                sx={{ mr: 1 }}
              >
                <AddIcon />
              </IconButton>
            </span>
          </Tooltip>
          <TextField
            fullWidth
            multiline
            maxRows={5}
            value={message}
            disabled={!settingsConfigured || isLoading}
            placeholder="Type a message (Shift+Enter for newline)"
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
              }
            }}
          />
          <IconButton aria-label="Stop generation" color="secondary" onClick={cancel} disabled={!isLoading} sx={{ ml: 1 }}>
            <StopIcon />
          </IconButton>
          <IconButton aria-label="Send message" color="primary" onClick={sendMessage} disabled={(!message.trim() && !attachments.length) || !settingsConfigured || isLoading} sx={{ ml: 1 }}>
            {isLoading ? <CircularProgress size={24} /> : <SendIcon />}
          </IconButton>
        </Box>
        <Box data-testid="model-selector-row" sx={{ display: 'flex', alignItems: 'flex-start', mt: 1.5 }}>
          <TextField
            select
            label="Model"
            size="small"
            value={modelKey(selectedModel)}
            disabled={isLoading || !availableModels.length}
            onChange={(event) => {
              const [apiType, model] = JSON.parse(event.target.value) as [ModelSelection['apiType'], string];
              const selection = { apiType, model };
              setSelectedModel(selection);
              socket?.emit('setSessionModel', { sessionId: session.id, selectedModel: selection });
            }}
            sx={{ width: { xs: '100%', sm: 320 }, maxWidth: '100%' }}
          >
            {availableModels.map((selection) => (
              <MenuItem key={modelKey(selection)} value={modelKey(selection)}>
                {selection.model} · {API_LABELS[selection.apiType]}
              </MenuItem>
            ))}
          </TextField>
          <Tooltip title="Switching models rebuilds agent state and replays completed text.">
            <IconButton
              aria-label="Model switching information"
              size="small"
              sx={{ ml: 0.5, mt: 0.5 }}
            >
              <InfoOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      </Paper>

      <Dialog open={showToolSelector} onClose={() => setShowToolSelector(false)} maxWidth="md" fullWidth>
        <DialogTitle>Select MCP Tools</DialogTitle>
        <DialogContent>
          {Object.entries(groupedTools).map(([serverId, tools]) => {
            const selectedCount = tools.filter((tool) => selectedTools.some((selected) => selected.id === tool.qualifiedId)).length;
            return (
              <Box key={serverId} sx={{ mb: 3 }}>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={selectedCount === tools.length}
                      indeterminate={selectedCount > 0 && selectedCount < tools.length}
                      onChange={() => toggleServer(serverId, tools)}
                    />
                  }
                  label={<Typography variant="h6">{tools[0]?.serverName || serverId}</Typography>}
                />
                <FormGroup sx={{ ml: 4 }}>
                  {tools.map((tool) => (
                    <FormControlLabel
                      key={tool.qualifiedId}
                      control={
                        <Checkbox
                          checked={selectedTools.some((selected) => selected.id === tool.qualifiedId)}
                          onChange={(event) => toggleTool(tool, event.target.checked)}
                        />
                      }
                      label={
                        <Box>
                          <Box sx={{ display: 'flex', alignItems: 'center' }}>
                            <Typography>{tool.displayName}</Typography>
                            <Tooltip title="Copy qualified tool ID">
                              <IconButton
                                size="small"
                                aria-label={`Copy qualified ID for ${tool.displayName}`}
                                onClick={(event) => {
                                  event.preventDefault();
                                  event.stopPropagation();
                                  navigator.clipboard.writeText(tool.qualifiedId);
                                }}
                              >
                                <ContentCopyIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </Box>
                          <Typography variant="body2" color="text.secondary">{tool.description}</Typography>
                        </Box>
                      }
                    />
                  ))}
                </FormGroup>
              </Box>
            );
          })}
          {!availableTools.length && <Typography>No tools available.</Typography>}
        </DialogContent>
        <DialogActions><Button onClick={() => setShowToolSelector(false)}>Close</Button></DialogActions>
      </Dialog>

      <Dialog open={!!approvalBatch} onClose={() => undefined} maxWidth="sm" fullWidth>
        <DialogTitle>Tool Execution Approval</DialogTitle>
        <DialogContent>
          <Typography sx={{ mb: 2 }}>Review every requested tool call. Unchecked calls are denied.</Typography>
          {approvalBatch?.requests.map((request) => (
            <Paper key={request.id} variant="outlined" sx={{ p: 2, mb: 1 }}>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={!!approvalDecisions[request.id]}
                    onChange={(event) => setApprovalDecisions((current) => ({ ...current, [request.id]: event.target.checked }))}
                  />
                }
                label={request.name}
              />
              {request.serverLabel && <Typography variant="body2">Server: {request.serverLabel}</Typography>}
              <pre style={{ overflow: 'auto', margin: 0 }}>{JSON.stringify(request.arguments, null, 2)}</pre>
            </Paper>
          ))}
        </DialogContent>
        <DialogActions>
          <Button color="error" onClick={() => resolveApproval('deny')}>Deny all</Button>
          <Button onClick={() => resolveApproval('submit')}>Submit decisions</Button>
          <Button variant="contained" color="warning" onClick={() => resolveApproval('always')}>Always allow all</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ChatInterface;

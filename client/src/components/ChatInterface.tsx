import React, { useState, useRef, useEffect } from 'react';
import {
  Box,
  Paper,
  TextField,
  Button,
  Typography,
  List,
  ListItem,
  Avatar,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  FormGroup,
  FormControlLabel,
  Checkbox,
  Alert,
  CircularProgress,
} from '@mui/material';
import SendIcon from '@mui/icons-material/Send';
import PersonIcon from '@mui/icons-material/Person';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import BuildIcon from '@mui/icons-material/Build';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import Tooltip from '@mui/material/Tooltip';
import { Socket } from 'socket.io-client';
import MarkdownRenderer from './MarkdownRenderer';
import StopIcon from '@mui/icons-material/Stop';

import { ChatSession, ChatMessage, MCPTool, AzureConfig, SelectedTool, ApprovalRequest } from '../types';

// Helper function to check for effectively missing Azure config
const isChatInterfaceAzureConfigEffectivelyMissing = (config: AzureConfig | null): boolean => {
  if (!config) return true;
  return !config.endpoint || !config.deployment; // API key might be handled by server/env
};

interface ChatInterfaceProps {
  session: ChatSession;
  availableTools: MCPTool[];
  azureConfig: AzureConfig | null;
  socket: Socket | null;
}

const ChatInterface: React.FC<ChatInterfaceProps> = ({
  session,
  availableTools,
  azureConfig,
  socket,
}) => {
  const [message, setMessage] = useState('');
  const [selectedTools, setSelectedTools] = useState<SelectedTool[]>([]);
  const [showToolSelector, setShowToolSelector] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [approvalRequest, setApprovalRequest] = useState<{
    sessionId: string;
    approvalRequest: ApprovalRequest;
    responseId: string;
  } | null>(null);
  const [pendingApproval,setPendingApproval]=useState<any|null>(null);
  const [pendingToolNames, setPendingToolNames] = useState<string[]>([]);
  // Track "always approve" preference per session instead of a single global flag
  // key = session.id, value = whether tool calls should be auto-approved for that session
  const [alwaysApproveSessions, setAlwaysApproveSessions] = useState<Record<string, boolean>>({});

  // Derived flag: whether to auto-approve tool calls for current session
  const alwaysApprove = alwaysApproveSessions[session.id] || session.autoApproveAll || false;

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const configIsEffectivelyMissing = isChatInterfaceAzureConfigEffectivelyMissing(azureConfig);

  useEffect(() => {
    if (!socket) return;

    socket.on('messageResponse', (data: any) => {
      setIsLoading(false);
      setPendingToolNames([]);
    });

    const toolStartedHandler = (data: { sessionId: string; toolName: string }) => {
      if (data.sessionId !== session.id) return;
      setPendingToolNames(prev => [...prev, data.toolName]);
    };
    socket.on('toolStarted', toolStartedHandler);

    const handler = (req: any) => {
      if (alwaysApprove) {
        socket.emit('approvalResult', { id: req.id, approved: true, approveAll: true });
      } else {
        setPendingApproval(req);
      }
    };
    socket.on('approvalRequired', handler);

    socket.on('error', (error: { message: string }) => {
      setIsLoading(false);
      console.error('[ChatInterface] Received error:', error);
      setErrorMessage(error.message);
      // 5秒後にエラーメッセージを自動で非表示にする
      setTimeout(() => setErrorMessage(null), 5000);
    });

    return () => {
      socket.off('messageResponse');
      socket.off('approvalRequired', handler);
      socket.off('error');
      socket.off('toolStarted', toolStartedHandler);
    };
  }, [socket, alwaysApprove, session.id]);

  useEffect(() => {
    scrollToBottom();
  }, [session.messages]);

  // Remove selected tools that disappear from availableTools (e.g., server removed)
  useEffect(() => {
    setSelectedTools(prev => prev.filter(sel =>
      availableTools.some(av => av.serverId === sel.serverId && av.name === sel.name)
    ));
  }, [availableTools]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleSendMessage = () => {
    if (configIsEffectivelyMissing) {
      console.warn('[ChatInterface] handleSendMessage: Azure config is effectively missing. Message not sent.');
      // Optionally, show an alert to the user here if not already visible
      return;
    }
    if (!message.trim() || !socket || !azureConfig) { // azureConfig check here is now a bit redundant but harmless
        console.warn('[ChatInterface] handleSendMessage: Basic send conditions not met (message, socket, or azureConfig).');
        return;
    }

    setIsLoading(true);
    setErrorMessage(null); // エラーメッセージをクリア
    socket.emit('sendMessage', {
      sessionId: session.id,
      message: message.trim(),
      selectedTools,
      azureConfig,
    });

    setMessage('');
  };

  const handleKeyPress = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSendMessage();
    }
  };

  const handleToolSelection = (tool: MCPTool, selected: boolean) => {
    if (selected) {
      setSelectedTools(prev => [
        ...prev,
        {
          id: `${tool.serverId}-${tool.name}`,
          serverId: tool.serverId,
          serverName: tool.serverName,    // 追加
          name: tool.name,
          description: tool.description,
          parameters: tool.parameters,
        }
      ]);
    } else {
      setSelectedTools(prev => 
        prev.filter(t => !(t.serverId === tool.serverId && t.name === tool.name))
      );
    }
  };

  const handleApproval = (approved: boolean, approveAll: boolean = false) => {
    if (!approvalRequest || !socket) return;

    socket.emit('approveToolCall', {
      sessionId: approvalRequest.sessionId,
      approvalRequestId: approvalRequest.approvalRequest.id,
      approved,
      approveAll,
    });

    if (approveAll) {
      setAlwaysApproveSessions(prev => ({ ...prev, [session.id]: true }));
    }
    setApprovalRequest(null);
    setIsLoading(true);
  };

  const sendApproval = (approved: boolean, approveAll: boolean = false) => {
    if (!socket || !pendingApproval) return;
    if (approveAll) {
      setAlwaysApproveSessions(prev => ({ ...prev, [session.id]: true }));
    }
    socket.emit('approvalResult', { id: pendingApproval.id, approved, approveAll });
    setPendingApproval(null);
  };

  const handleStopGeneration = () => {
    if (!socket) return;
    socket.emit('stopGeneration', { sessionId: session.id });
    setIsLoading(false);
    setPendingToolNames([]);
  };

  const formatTimestamp = (timestamp: Date) => {
    return new Date(timestamp).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const groupedTools = availableTools.reduce((acc, tool) => {
    if (!acc[tool.serverId]) {
      acc[tool.serverId] = [];
    }
    acc[tool.serverId].push(tool);
    return acc;
  }, {} as Record<string, MCPTool[]>);

  // Calculate check state at MCP server level
  const getServerCheckState = (serverId: string, tools: MCPTool[]) => {
    const selectedServerTools = selectedTools.filter(t => t.serverId === serverId);
    const allServerTools = tools;
    
    if (selectedServerTools.length === 0) {
      return 'unchecked'; // nothing selected
    } else if (selectedServerTools.length === allServerTools.length) {
      return 'checked'; // all selected
    } else {
      return 'indeterminate'; // partially selected
    }
  };

  // Toggle selection for entire MCP server
  const handleServerSelection = (serverId: string, tools: MCPTool[]) => {
    const checkState = getServerCheckState(serverId, tools);
    
    if (checkState === 'unchecked' || checkState === 'indeterminate') {
      // If not or partially selected, select all tools
      const newSelectedTools = tools.map(tool => ({
        id: `${tool.serverId}-${tool.name}`,
        serverId: tool.serverId,
        serverName: tool.serverName,
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters,
      }));
      
      setSelectedTools(prev => [
        ...prev.filter(t => t.serverId !== serverId), // remove existing tools from same server
        ...newSelectedTools // add new tools
      ]);
    } else {
      // If all selected, clear all selections
      setSelectedTools(prev => prev.filter(t => t.serverId !== serverId));
    }
  };

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6" gutterBottom>
          {session.name}
        </Typography>
        


        <Button
          variant="outlined"
          startIcon={<BuildIcon />}
          onClick={() => setShowToolSelector(true)}
          sx={{ mt: 1 }}
          size="small"
          disabled={configIsEffectivelyMissing} // Disable if config missing
        >
          {availableTools.length > 0 
            ? `Select tools (${selectedTools.length} selected / ${availableTools.length} available)` 
            : 'Select tools (none available)'
          }
        </Button>

        {availableTools.length === 0 && !configIsEffectivelyMissing && (
          <Alert severity="info" sx={{ mt: 1 }}>
            No tools could be retrieved from the connected MCP servers.
          </Alert>
        )}

        {configIsEffectivelyMissing && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            Azure OpenAI configuration is incomplete. Please review the main Azure settings.
          </Alert>
        )}

        {errorMessage && (
          <Alert 
            severity="error" 
            sx={{ mt: 1 }}
            onClose={() => setErrorMessage(null)}
          >
            {errorMessage}
          </Alert>
        )}
      </Box>

      {/* Messages */}
      <Box sx={{ flexGrow: 1, overflow: 'auto', p: 2 }}>
        <List>
          {(session.messages as any[]).filter(m=>m.role!=='function').map((msg: ChatMessage, index: number) => (
            <ListItem
              key={index}
              sx={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                mb: 1,
              }}
            >              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  maxWidth: '95%',
                  width: '100%',
                  flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                }}
              >
                <Avatar
                  sx={{
                    bgcolor: msg.role === 'user' ? 'primary.main' : 'secondary.main',
                    mx: 1,
                    flexShrink: 0,
                  }}
                >
                  {msg.role === 'user' ? <PersonIcon /> : <SmartToyIcon />}
                </Avatar>
                  <Paper
                  sx={{
                    p: 2,
                    bgcolor: msg.role === 'user' ? 'primary.main' : 'grey.100',
                    color: msg.role === 'user' ? 'white' : 'inherit',
                    maxWidth: 'calc(100% - 56px)',
                    width: 'fit-content',
                    minWidth: 0,
                    overflow: 'hidden',
                    wordWrap: 'break-word',
                    overflowWrap: 'break-word',
                    boxSizing: 'border-box',
                    userSelect: 'text', // Allow selecting and copying message text
                    // 追加の制限でコンテンツが幅を超えないように
                    '& *': {
                      maxWidth: '100%',
                      boxSizing: 'border-box',
                      userSelect: 'text', // Ensure descendants are selectable
                    }
                  }}
                >
                  <MarkdownRenderer 
                    content={msg.content} 
                    color={msg.role === 'user' ? 'white' : 'inherit'}
                  />
                  
                  {msg.toolCalls && msg.toolCalls.length > 0 && (
                    <Typography variant="caption" color="text.secondary">
                      tools: {msg.toolCalls.join(', ')}
                    </Typography>
                  )}
                  
                  <Typography
                    variant="caption"
                    sx={{
                      display: 'block',
                      mt: 1,
                      opacity: 0.7,
                    }}
                  >
                    {formatTimestamp(msg.timestamp)}
                  </Typography>
                </Paper>
              </Box>
            </ListItem>
          ))}
          
          {isLoading && (
            <ListItem sx={{ justifyContent: 'center' }}>
              <CircularProgress size={24} />
              <Typography variant="body2" sx={{ ml: 1 }}>
                Generating response...
              </Typography>
            </ListItem>
          )}
          
          {/* Pending tool names while awaiting assistant response */}
          {pendingToolNames.length > 0 && (
            <ListItem
              sx={{
                display: 'flex',
                justifyContent: 'flex-start',
                mb: 1,
              }}
            >
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  maxWidth: '70%',
                  width: '100%',
                }}
              >
                <Avatar sx={{ bgcolor: 'secondary.main', mx: 1, flexShrink: 0 }}>
                  <SmartToyIcon />
                </Avatar>
                <Paper sx={{ p: 2, bgcolor: 'grey.100', maxWidth: 'calc(100% - 56px)', width: 'fit-content' }}>
                  {pendingToolNames.map((t, idx) => (
                    <Typography key={idx} variant="body2" color="text.secondary">
                      Tool: {t}
                    </Typography>
                  ))}
                </Paper>
              </Box>
            </ListItem>
          )}

          <div ref={messagesEndRef} />
        </List>
      </Box>

      {/* Input Area */}
      <Paper sx={{ p: 2, mt: 'auto', backgroundColor: 'background.default' }}>
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <TextField
            fullWidth
            variant="outlined"
            placeholder="Type a message (Shift+Enter for newline)"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyPress={handleKeyPress}
            multiline
            maxRows={5}
            disabled={configIsEffectivelyMissing || isLoading} // Disable if config missing or loading
          />
          <IconButton
            color="secondary"
            onClick={handleStopGeneration}
            disabled={!isLoading}
            sx={{ ml: 1 }}
          >
            <StopIcon />
          </IconButton>
          <IconButton
            color="primary"
            onClick={handleSendMessage}
            disabled={!message.trim() || configIsEffectivelyMissing || isLoading} // Disable if config missing or loading
            sx={{ ml: 1 }}
          >
            {isLoading ? <CircularProgress size={24} /> : <SendIcon />}
          </IconButton>
        </Box>
      </Paper>

      {/* Tool Selector Dialog */}
      <Dialog
        open={showToolSelector}
        onClose={() => setShowToolSelector(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Select MCP Tools</DialogTitle>
        <DialogContent>
          {Object.keys(groupedTools).length === 0 ? (
            <Typography>No tools available</Typography>
          ) : (
            Object.entries(groupedTools).map(([serverId, tools]) => {
              // serverName があれば使い、なければ serverId をフォールバック
              const displayName = tools[0]?.serverName || serverId;
              const checkState = getServerCheckState(serverId, tools);
              
              return (
                <Box key={serverId} sx={{ mb: 3 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                    <Checkbox
                      checked={checkState === 'checked'}
                      indeterminate={checkState === 'indeterminate'}
                      onChange={() => handleServerSelection(serverId, tools)}
                      sx={{ mr: 1 }}
                    />
                    <Typography variant="h6" gutterBottom sx={{ mb: 0 }}>
                      {displayName}
                    </Typography>
                  </Box>
                  <FormGroup sx={{ ml: 4 }}>
                    {tools.map((tool) => (
                      <FormControlLabel
                        key={`${tool.serverId}-${tool.name}`}
                        control={
                          <Checkbox
                            checked={selectedTools.some(
                              t => t.serverId === tool.serverId && t.name === tool.name
                            )}
                            onChange={(e) => handleToolSelection(tool, e.target.checked)}
                          />
                        }
                        label={
                          <Box>
                            <Box sx={{ display: 'flex', alignItems: 'center' }}>
                              <Typography variant="body1" sx={{ mr: 1 }}>
                                {tool.name}
                              </Typography>
                              <Tooltip title="Copy tool name">
                                <IconButton
                                  size="small"
                                  onClick={(e) => {
                                    e.stopPropagation(); // Prevent checkbox toggle
                                    navigator.clipboard.writeText(tool.name);
                                  }}
                                >
                                  <ContentCopyIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            </Box>
                            {tool.description && (
                              <Typography variant="body2" color="text.secondary">
                                {tool.description}
                              </Typography>
                            )}
                          </Box>
                        }
                      />
                    ))}
                  </FormGroup>
                </Box>
              );
            })
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowToolSelector(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      {/* Approval Dialog */}
      <Dialog
        open={!!approvalRequest}
        onClose={() => {}} // Prevent closing without decision
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Tool Execution Approval</DialogTitle>
        <DialogContent>
          {approvalRequest && (
            <Box>
              <Typography variant="body1" gutterBottom>
                Do you allow the execution of the following tool?
              </Typography>
              <Typography variant="h6" gutterBottom>
                {approvalRequest.approvalRequest.name}
              </Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Server: {approvalRequest.approvalRequest.server_label}
              </Typography>
              <Typography variant="body2" gutterBottom>
                Arguments:
              </Typography>
              <Paper sx={{ p: 2, bgcolor: 'grey.100' }}>
                <pre style={{ margin: 0, fontSize: '0.875rem' }}>
                  {JSON.stringify(approvalRequest.approvalRequest.arguments, null, 2)}
                </pre>
              </Paper>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => handleApproval(false)} color="error">
            Deny
          </Button>
          <Button onClick={() => handleApproval(true, true)} color="warning">
            Always Allow
          </Button>
          <Button onClick={() => handleApproval(true)} color="primary" variant="contained">
            Execute
          </Button>
        </DialogActions>
      </Dialog>

      {/* Pending Approval Dialog */}
      {pendingApproval && (
        <Dialog open onClose={()=>sendApproval(false)}>
          <DialogTitle>Confirm Tool Execution</DialogTitle>
          <DialogContent>
            <Typography>Tool: {pendingApproval.name}</Typography>
            <pre style={{background:'#eee',padding:8,borderRadius:4,maxHeight:200,overflow:'auto'}}> 
{JSON.stringify(pendingApproval.arguments,null,2)}
</pre>
          </DialogContent>
          <DialogActions>
            <Button onClick={()=>sendApproval(false)}>Deny</Button>
            <Button onClick={()=>sendApproval(true,false)} color="warning">Allow</Button>
            <Button onClick={()=>sendApproval(true,true)} color="error">Always Allow</Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  );
};

export default ChatInterface;

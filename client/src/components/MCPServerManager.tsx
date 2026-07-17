import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Box,
  Typography,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  IconButton,
  Chip,
  Paper,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Alert,
  Tooltip,
} from '@mui/material';
import {
  Add as AddIcon,
  Delete as DeleteIcon,
  ExpandMore as ExpandMoreIcon,
  Storage as StorageIcon,
  ErrorOutline as ErrorOutlineIcon,
} from '@mui/icons-material';

import { MCPServerConfig } from '../types';
import KeyValueRows, { KeyValueRow, recordToRows, rowsToRecord } from './KeyValueRows';

interface MCPServerManagerProps {
  open: boolean;
  onClose: () => void;
  servers: MCPServerConfig[];
  socket: any;
}

const MCPServerManager: React.FC<MCPServerManagerProps> = ({
  open,
  onClose,
  servers,
  socket,
}) => {
  const [error, setError] = useState<string>('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [showSavedServers, setShowSavedServers] = useState(false);
  const [savedServers, setSavedServers] = useState<MCPServerConfig[]>([]);
  const [serverStatuses, setServerStatuses] = useState<Record<string, { status: string; error?: string }>>({});
  const [statusDialogServerId, setStatusDialogServerId] = useState<string | null>(null);
  const [statusDialogTitle, setStatusDialogTitle] = useState<string>('');

  // Helper to append PATH hint when "such file or directory" errors occur (often on macOS)
  function appendPathHint(message: string) {
    const pattern = /(\[?errno\s*2\]?|enoent|no such file or directory)/i;
    if (pattern.test(message)) {
      return `${message}\nTry adding the command dir to env "PATH",\ne.g. Key:"PATH" Value:"/opt/homebrew/bin:/bin:/usr/bin:/usr/local/bin:$PATH"`;
    }
    return message;
  }

  // Listen for errors from server
  useEffect(() => {
    if (!socket) return;
    const handleError = (err: { message: string }) => {
      setError(appendPathHint(err.message));
    };
    socket.on('error', handleError);
    return () => { socket.off('error', handleError); };
  }, [socket]);

  // Listen for saved servers events
  useEffect(() => {
    if (!socket) return;
    
    const handleSavedServers = (servers: MCPServerConfig[]) => {
      setSavedServers(servers);
    };
    
    const handleSavedServerDeleted = (serverName: string) => {
      setSavedServers(prev => prev.filter(server => server.name !== serverName));
    };
    
    socket.on('savedServers', handleSavedServers);
    socket.on('savedServerDeleted', handleSavedServerDeleted);
    
    return () => {
      socket.off('savedServers', handleSavedServers);
      socket.off('savedServerDeleted', handleSavedServerDeleted);
    };
  }, [socket]);

  // Listen for server status updates
  useEffect(() => {
    if (!socket) return;

    const handleServerStatus = (res: { id?: string; name?: string; status: string; error?: string }) => {
      const key = res.id || res.name || '';
      if (!key) return;
      setServerStatuses(prev => ({
        ...prev,
        [key]: {
          status: res.status,
          error: res.error ? appendPathHint(res.error) : undefined,
        },
      }));
    };

    socket.on('mcpServerStatus', handleServerStatus);
    return () => { socket.off('mcpServerStatus', handleServerStatus); };
  }, [socket]);

  // Fetch saved servers when dialog opens
  useEffect(() => {
    if (open && socket) {
      socket.emit('getSavedServers');
      socket.emit('getMCPServers');
    }
  }, [open, socket]);
  const [newServer, setNewServer] = useState<Partial<MCPServerConfig>>({
    name: '',
    transport: 'http',
    command: '',
    args: [],
    env: {},
    url: '',
    headers: {},
  });
  const [envRows, setEnvRows] = useState<KeyValueRow[]>(recordToRows());
  const [headerRows, setHeaderRows] = useState<KeyValueRow[]>(recordToRows());
  const [argsInput, setArgsInput] = useState('');

  const rowsAreInvalid = (rows: KeyValueRow[]) => {
    const active = rows.filter((row) => row.key || row.value);
    const keys = active.map((row) => row.key.trim());
    return active.some((row) => !row.key.trim()) || new Set(keys).size !== keys.length;
  };

  const handleAddServer = () => {
    if (!socket || !newServer.name || !newServer.transport) return;
    if (rowsAreInvalid(envRows) || rowsAreInvalid(headerRows)) {
      setError('Environment variable and header keys must be non-empty and unique.');
      return;
    }
    if (newServer.transport === 'stdio' && !newServer.command?.trim()) {
      setError('Executable Path is required for STDIO.');
      return;
    }
    if (newServer.transport !== 'stdio' && !newServer.url?.trim()) {
      setError('URL is required for HTTP transport.');
      return;
    }

    const args = argsInput
      .split(' ')
      .map(arg => arg.trim())
      .filter(arg => arg.length > 0);

    const serverConfig: MCPServerConfig = {
      ...newServer,
      args,
      env: rowsToRecord(envRows),
      headers: rowsToRecord(headerRows),
    } as MCPServerConfig;

    // Add debug log
    socket.emit('registerMCPServer', serverConfig);
    
    setNewServer({
      name: '',
      transport: 'http',
      command: '',
      args: [],
      env: {},
      url: '',
      headers: {},
    });
    setArgsInput('');
    setEnvRows(recordToRows());
    setHeaderRows(recordToRows());
    setShowAddForm(false);
  };

  const handleRemoveServer = (serverId: string) => {
    if (!socket) return;
    socket.emit('removeMCPServer', serverId);
  };

  const handleAddSavedServer = (savedServer: MCPServerConfig) => {
    if (!socket) return;
    
    // Generate new ID for the server instance
    const serverConfig = {
      ...savedServer,
      id: undefined, // Let the server generate a new ID
    };
    
    socket.emit('registerMCPServer', serverConfig);
    setShowSavedServers(false);
  };

  const handleDeleteSavedServer = (serverName: string) => {
    if (!socket) return;
    socket.emit('deleteSavedServer', serverName);
  };

  const getTransportColor = (transport: string) => {
    switch (transport) {
      case 'stdio': return 'primary';
      case 'sse': return 'secondary';
      case 'http': return 'success';
      default: return 'default';
    }
  };

  const requestServerStatus = (serverId: string) => {
    if (!socket) return;
    socket.emit('getMCPServerStatus', serverId);
  };

  const renderStatusChip = (key: string) => {
    const info = serverStatuses[key];
    if (!info) return null;
    let color: 'default' | 'success' | 'warning' | 'error' = 'default';
    switch (info.status) {
      case 'connected':
        color = 'success';
        break;
      case 'connecting':
        color = 'warning';
        break;
      case 'error':
        color = 'error';
        break;
    }
    return (
      <Tooltip title={info.error || info.status} arrow>
        <Chip label={info.status} color={color as any} size="small" />
      </Tooltip>
    );
  };

  const isErrorStatus = (info?: { status: string; error?: string }) => {
    if (!info) return false;
    return !!info.error || ['error', 'failed', 'disconnected'].includes(info.status);
  };

  const handleShowInfo = (key: string, name: string) => {
    setStatusDialogServerId(key);
    setStatusDialogTitle(`${name} – Status`);
    requestServerStatus(key);
  };

  const renderStatusDialogBody = () => {
    if (!statusDialogServerId) return '';
    const info = serverStatuses[statusDialogServerId];
    if (!info) return 'Fetching status…';
    const bodyLines = [`Status: ${info.status}`];
    if (info.error) bodyLines.push(`Error: ${info.error}`);
    return bodyLines.join('\n');
  };

  // Fetch statuses for all connected servers when dialog opens or list updates
  useEffect(() => {
    if (!open || !socket) return;
    servers.forEach((srv) => {
      socket.emit('getMCPServerStatus', getServerKey(srv));
    });
  }, [open, servers, socket]);

  // Request statuses when server list updates from backend
  useEffect(() => {
    if (!socket) return;
    const handleMcpServers = (list: MCPServerConfig[]) => {
      list.forEach((srv) => {
        socket.emit('getMCPServerStatus', getServerKey(srv));
      });
    };
    socket.on('mcpServers', handleMcpServers);
    return () => {
      socket.off('mcpServers', handleMcpServers);
    };
  }, [socket]);

  function getServerKey(srv: MCPServerConfig) {
    return srv.id || srv.name;
  }

  return (
    <Dialog open={open} onClose={() => { setError(''); onClose(); }} maxWidth="md" fullWidth>
      <DialogTitle>MCP Server Manager</DialogTitle>
      <DialogContent sx={{ userSelect: 'text' }}>
        {error && (
          <Alert
            severity="error"
            onClose={() => setError('')}
            sx={{ mb: 2, userSelect: 'text' }}
          >
            {error}
          </Alert>
        )}
        <Box sx={{ mb: 3, display: 'flex', gap: 2 }}>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setShowAddForm(true)}
            disabled={showAddForm || showSavedServers}
          >
            Add New Server
          </Button>
          <Button
            variant="outlined"
            startIcon={<StorageIcon />}
            onClick={() => setShowSavedServers(true)}
            disabled={showAddForm || showSavedServers}
          >
            Add Saved Server
          </Button>
        </Box>

        {showAddForm && (
          <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" gutterBottom>
              New MCP Server
            </Typography>
            
            <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
              <TextField
                fullWidth
                label="Server Name"
                value={newServer.name}
                onChange={(e) => setNewServer((prev: Partial<MCPServerConfig>) => ({ ...prev, name: e.target.value }))}
              />
              
              <FormControl fullWidth>
                <InputLabel>Protocol</InputLabel>
                <Select
                  value={newServer.transport}
                  label="Protocol"
                  onChange={(e) => setNewServer((prev: Partial<MCPServerConfig>) => ({ ...prev, transport: e.target.value as any }))}
                >
                  <MenuItem value="http">HTTP</MenuItem>
                  <MenuItem value="stdio">STDIO</MenuItem>
                  <MenuItem value="sse">SSE (Streamable HTTP alias)</MenuItem>
                </Select>
              </FormControl>
            </Box>

            {/* Transport-specific fields */}
            {newServer.transport === 'stdio' && (
              <Box>
                <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
                  <TextField
                    fullWidth
                    label="Executable Path"
                    value={newServer.command}
                    onChange={(e) => setNewServer((prev: Partial<MCPServerConfig>) => ({ ...prev, command: e.target.value }))}
                    placeholder="/path/to/mcp-server"
                  />
                  
                  <TextField
                    fullWidth
                    label="Arguments (space-separated)"
                    value={argsInput}
                    onChange={(e) => setArgsInput(e.target.value)}
                    placeholder="--config config.json"
                  />
                </Box>

                <TextField
                  fullWidth
                  label="Working Directory (optional)"
                  value={newServer.cwd || ''}
                  onChange={(e) => setNewServer((prev: Partial<MCPServerConfig>) => ({ ...prev, cwd: e.target.value }))}
                  sx={{ mb: 2 }}
                />

                {/* Environment Variables */}
                <Accordion>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography component="div">Environment Variables</Typography>
                  </AccordionSummary>
                  <AccordionDetails>
                    <KeyValueRows
                      rows={envRows}
                      onChange={setEnvRows}
                      keyLabel="Environment variable"
                      valueLabel="Value"
                    />
                  </AccordionDetails>
                </Accordion>
              </Box>
            )}

            {(newServer.transport === 'sse' || newServer.transport === 'http') && (
              <Box>
                {newServer.transport === 'sse' && (
                  <Alert severity="warning" sx={{ mb: 2 }}>
                    SSE is a display alias for MCP Streamable HTTP. Legacy GET + POST SSE endpoints are not supported.
                  </Alert>
                )}
                <TextField
                  fullWidth
                  label="URL"
                  value={newServer.url}
                  onChange={(e) => setNewServer((prev: Partial<MCPServerConfig>) => ({ ...prev, url: e.target.value }))}
                  placeholder="http://localhost:3000"
                  sx={{ mb: 2 }}
                />

                {/* HTTP Headers */}
                <Accordion>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography component="div">
                      HTTP Headers
                      {headerRows.some((row) => row.key.trim()) && (
                        <Chip 
                          label={headerRows.filter((row) => row.key.trim()).length}
                          size="small" 
                          color="primary" 
                          sx={{ ml: 1 }}
                        />
                      )}
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails>
                    <KeyValueRows
                      rows={headerRows}
                      onChange={setHeaderRows}
                      keyLabel="Header"
                      valueLabel="Value"
                    />
                  </AccordionDetails>
                </Accordion>
              </Box>
            )}

            <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
              <Button
                variant="contained"
                onClick={handleAddServer}
                disabled={!newServer.name || !newServer.transport}
              >
                Add
              </Button>
              <Button
                variant="outlined"
                onClick={() => {
                  setShowAddForm(false);
                  setNewServer({
                    name: '',
                    transport: 'http',
                    command: '',
                    args: [],
                    env: {},
                    url: '',
                    headers: {},
                  });
                  setArgsInput('');
                  setEnvRows(recordToRows());
                  setHeaderRows(recordToRows());
                }}
              >
                Cancel
              </Button>
            </Box>
          </Paper>
        )}

        {showSavedServers && (
          <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" gutterBottom>
              Saved Servers
            </Typography>
            
            {savedServers.length === 0 ? (
              <Alert severity="info" sx={{ mb: 2 }}>
                No saved MCP servers.
              </Alert>
            ) : (
              <List>
                {savedServers.map((server) => (
                  <ListItem key={server.name} divider>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Typography variant="subtitle1">{server.name}</Typography>
                          <Chip
                            label={server.transport.toUpperCase()}
                            color={getTransportColor(server.transport) as any}
                            size="small"
                          />
                        </Box>
                      }
                      primaryTypographyProps={{ component: 'div' }}
                      secondaryTypographyProps={{ component: 'div' }}
                      secondary={
                        <Box>
                          {server.transport === 'stdio' && (
                            <Typography variant="body2" color="text.secondary">
                              Command: {server.command} {server.args?.join(' ')}
                            </Typography>
                          )}
                          {(server.transport === 'sse' || server.transport === 'http') && (
                            <Typography variant="body2" color="text.secondary">
                              URL: {server.url}
                            </Typography>
                          )}
                        </Box>
                      }
                    />
                    <ListItemSecondaryAction>
                      <Box sx={{ display: 'flex', gap: 1 }}>
                        <Button
                          size="small"
                          variant="contained"
                          onClick={() => handleAddSavedServer(server)}
                        >
                          Add
                        </Button>
                        <IconButton onClick={() => handleDeleteSavedServer(server.name)}>
                          <DeleteIcon />
                        </IconButton>
                      </Box>
                    </ListItemSecondaryAction>
                  </ListItem>
                ))}
              </List>
            )}
            
            <Box sx={{ mt: 2 }}>
              <Button
                variant="outlined"
                onClick={() => setShowSavedServers(false)}
              >
                Close
              </Button>
            </Box>
          </Paper>
        )}

        {/* Server List */}
        <Typography variant="h6" gutterBottom>
          Connected Servers
        </Typography>

        {servers.length === 0 ? (
          <Alert severity="info">
            No connected MCP servers.
          </Alert>
        ) : (
          <List>
            {servers.map((server) => (
              <ListItem key={server.id} divider>
                <ListItemText
                  primary={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Typography variant="subtitle1">{server.name}</Typography>
                      <Chip
                        label={server.transport.toUpperCase()}
                        color={getTransportColor(server.transport) as any}
                        size="small"
                      />
                    </Box>
                  }
                  primaryTypographyProps={{ component: 'div' }}
                  secondaryTypographyProps={{ component: 'div' }}
                  secondary={
                    <Box>
                      {server.transport === 'stdio' && (
                        <Typography variant="body2" color="text.secondary">
                          Command: {server.command} {server.args?.join(' ')}
                        </Typography>
                      )}
                      {(server.transport === 'sse' || server.transport === 'http') && (
                        <>
                          <Typography variant="body2" color="text.secondary">
                            URL: {server.url}
                          </Typography>
                        </>
                      )}
                    </Box>
                  }
                />
                <ListItemSecondaryAction>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    {renderStatusChip(getServerKey(server))}
                    {isErrorStatus(serverStatuses[getServerKey(server)]) && (
                      <IconButton onClick={() => handleShowInfo(getServerKey(server), server.name)}>
                        <ErrorOutlineIcon color="error" />
                      </IconButton>
                    )}
                    <IconButton onClick={() => handleRemoveServer(server.id!)}>
                      <DeleteIcon />
                    </IconButton>
                  </Box>
                </ListItemSecondaryAction>
              </ListItem>
            ))}
          </List>
        )}
      </DialogContent>
      {statusDialogServerId && (
        <Dialog open={Boolean(statusDialogServerId)} onClose={() => setStatusDialogServerId(null)}>
          <DialogTitle>{statusDialogTitle}</DialogTitle>
          <DialogContent dividers>
            <Typography sx={{ whiteSpace: 'pre-wrap', userSelect: 'text' }}>
              {renderStatusDialogBody()}
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setStatusDialogServerId(null)}>Close</Button>
          </DialogActions>
        </Dialog>
      )}
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

export default MCPServerManager;

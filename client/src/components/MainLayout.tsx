import React, { useState, useEffect } from 'react';
import {
  Box,
  Drawer,
  AppBar,
  Toolbar,
  Typography,
  Button,
  Paper,
  Alert,
  Snackbar,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import SettingsIcon from '@mui/icons-material/Settings';

import { useSocket } from '../contexts/SocketContext';
import { ChatSession, MCPServerConfig, MCPTool, AzureConfig } from '../types';
import ChatInterface from './ChatInterface';
import MCPServerManager from './MCPServerManager';
import SessionList from './SessionList';
import AzureConfigDialog from './AzureConfigDialog';

const DRAWER_WIDTH = 300;

const backendUrl =
  (import.meta as any).env?.VITE_BACKEND_URL ||
  (process as any).env?.REACT_APP_SERVER_URL ||
  'http://localhost:3001';

const MainLayout: React.FC = () => {
  const { socket, isConnected } = useSocket();
  
  const [currentSession, setCurrentSession] = useState<ChatSession | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServerConfig[]>([]);
  const [availableTools, setAvailableTools] = useState<MCPTool[]>([]);
  const [azureConfig, setAzureConfig] = useState<AzureConfig | null>(() => {
    const storedConfig = localStorage.getItem('azureConfig');
    if (storedConfig) {
      try {
        const parsed = JSON.parse(storedConfig);
        // Check if the parsed config is effectively empty
        if (!parsed.endpoint || !parsed.deployment) {
          localStorage.removeItem('azureConfig');
          return null;
        }
        return parsed;
      } catch (e) {
        console.error('[MainLayout] Failed to parse Azure config from localStorage - details not logged for security');
        localStorage.removeItem('azureConfig');
        return null;
      }
    }
    return null;
  });  const [showMCPManager, setShowMCPManager] = useState(false);
  const [showAzureConfig, setShowAzureConfig] = useState(false);
  const [error, setError] = useState<string>('');
  const [serverAzureConfigured, setServerAzureConfigured] = useState<boolean | null>(null); // null = checking, true/false = result
  const [serverAzureConfig, setServerAzureConfig] = useState<AzureConfig | null>(null); // Server-side config from env vars

  // Check server Azure configuration on component mount
  // Runs when the component mounts and whenever the local Azure configuration changes
  useEffect(() => {
    const checkServerAzureConfig = async () => {
      try {
        const response = await fetch(`${backendUrl}/azure-config-status`);
        const data = await response.json();
        setServerAzureConfigured(data.isConfigured);
        
        // If server has config, also fetch the actual config values
        if (data.isConfigured) {
          try {
            const configResponse = await fetch(`${backendUrl}/azure-config`);
            const configData = toCamel(await configResponse.json());
            setServerAzureConfig(configData);
            
            // If no client-side config exists, use server config as default
            if (!azureConfig) {
              setAzureConfig(configData);
            }
          } catch (configError) {
            console.error('[MainLayout] Failed to fetch server Azure config values - details not logged for security');
          }
        }
      } catch (error) {
        console.error('[MainLayout] Failed to check server Azure config - details not logged for security');
        setServerAzureConfigured(false);
      }
    };

    checkServerAzureConfig();
  }, [azureConfig]); // run once on mount

  useEffect(() => {
    if (!socket) return;

    // Socket event listeners
    socket.on('sessionCreated', (session: ChatSession) => {
      setCurrentSession(session);
      setSessions(prev => [session, ...prev]);
    });

    socket.on('sessions', (sessionList: ChatSession[]) => {
      setSessions(sessionList);
    });

    socket.on('sessionLoaded', (session: ChatSession) => {
      setCurrentSession(session);
    });

    socket.on('sessionUpdated', (session: ChatSession) => {
      // Update sessions list always
      setSessions(prev => prev.map(s => s.id === session.id ? session : s));
      // Only replace currentSession if it is the one being updated
      setCurrentSession(prev => (prev && prev.id === session.id ? session : prev));
    });

    socket.on('mcpServers', (servers: MCPServerConfig[]) => {
      setMcpServers(servers);
      // Get tools for all servers
      servers.forEach(server => {
        if (server.id) {
          socket.emit('getMCPServerTools', server.id);
        }
      });
    });

    socket.on('mcpServerRegistered', (server: MCPServerConfig) => {
      setMcpServers(prev => [...prev, server]);
      if (server.id) {
        socket.emit('getMCPServerTools', server.id);
      }
    });

    socket.on('mcpServerTools', (data: { serverId: string; tools: MCPTool[] }) => {
      setAvailableTools(prev => [
        ...prev.filter(tool => tool.serverId !== data.serverId),
        ...data.tools
      ]);
    });

    socket.on('mcpServerRemoved', (serverId: string) => {
      setMcpServers(prev => prev.filter(s => s.id !== serverId));
      setAvailableTools(prev => prev.filter(tool => tool.serverId !== serverId));
    });

    socket.on('error', (error: { message: string }) => {
      setError(error.message);
    });

    // Initial data load
    console.log('[MainLayout] Emitting initial data requests');
    socket.emit('getSessions');
    socket.emit('getMCPServers');

    return () => {
      socket.off('sessionCreated');
      socket.off('sessions');
      socket.off('sessionLoaded');
      socket.off('sessionUpdated');
      socket.off('mcpServers');
      socket.off('mcpServerRegistered');
      socket.off('mcpServerTools');
      socket.off('mcpServerRemoved');
      socket.off('error');
    };
  }, [socket]);

  const handleNewSession = () => {
    if (socket) {
      socket.emit('createNewSession');
    }
  };

  const handleLoadSession = (sessionId: string) => {
    if (socket) {
      socket.emit('loadSession', sessionId);
    }
  };

  const handleDeleteSession = (sessionId: string) => {
    if (socket) {
      socket.emit('deleteSession', sessionId);
    }
    // Optionally clear current session if it's deleted
    if (currentSession?.id === sessionId) {
      setCurrentSession(null);
    }
  };

  const handleSaveAzureConfig = async (config: AzureConfig) => {
    try {
      // Save to server memory
      const response = await fetch(`${backendUrl}/azure-config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toSnake(config)),
      });
      const savedConfig: AzureConfig = toCamel(await response.json());
      setServerAzureConfig(savedConfig);
      setServerAzureConfigured(true);
      // Also save to client storage
      setAzureConfig(savedConfig);
      localStorage.setItem('azureConfig', JSON.stringify(savedConfig));
      setShowAzureConfig(false);
    } catch (err) {
      console.error('[MainLayout] Failed to save Azure config to server - details not logged for security');
      setError((err as Error).message);
    }
  };

  const handleCloseError = () => {
    setError('');
  };

  const isAzureConfigEffectivelyMissing = (config: AzureConfig | null): boolean => {
    // If server has Azure configuration, client-side config is not strictly required
    if (serverAzureConfigured === true) {
      return false;
    }
    
    if (!config) return true;
    // Consider config missing if essential fields are not present
    return !config.endpoint || !config.deployment; // apiKey can be from .env on server
  };

  const azureConfigMissing = isAzureConfigEffectivelyMissing(azureConfig);

  return (
    <Box sx={{ display: 'flex', height: '100vh' }}>
      {/* App Bar */}
      <AppBar position="fixed" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
        <Toolbar>
          <Typography variant="h6" noWrap component="div" sx={{ flexGrow: 1 }}>
            MCP Client for Azure
          </Typography>
          
          {!isConnected && (
            <Alert severity="error" sx={{ mr: 2 }}>
              Cannot connect to server
            </Alert>
          )}

          <Button
            color="inherit"
            startIcon={<SettingsIcon />}
            onClick={() => setShowAzureConfig(true)}
            sx={{ mr: 1 }}
          >
            Azure Settings
          </Button>          <Button
            color="inherit"
            onClick={() => setShowMCPManager(true)}            sx={{ mr: 2 }}
          >
            MCP Servers
          </Button>

          {/* Connection Status Indicator */}
          <Box sx={{ mr: 2, display: 'flex', alignItems: 'center' }}>
            {/* Show red indicator only when disconnected */}
            {!isConnected && (
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  backgroundColor: 'error.main',
                  mr: 1,
                }}
              />
            )}
             <Typography variant="body2" sx={{ fontSize: '0.8rem' }}>
               {!isConnected && 'Cannot connect to server'}
             </Typography>
           </Box>

          <Button
            color="inherit"
            startIcon={<AddIcon />}
            onClick={handleNewSession}
            disabled={!isConnected}
          >
            New Chat
          </Button>
        </Toolbar>
      </AppBar>

      {/* Sidebar */}
      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          [`& .MuiDrawer-paper`]: {
            width: DRAWER_WIDTH,
            boxSizing: 'border-box',
          },
        }}
      >
        <Toolbar />
        <Box sx={{ overflow: 'auto', p: 1 }}>
          <SessionList
            sessions={sessions}
            currentSession={currentSession}
            onSelectSession={handleLoadSession}
            onDeleteSession={handleDeleteSession}
          />
        </Box>
      </Drawer>

      {/* Main Content */}
      <Box component="main" sx={{ flexGrow: 1, p: 3 }}>
        <Toolbar />
        
        {currentSession && !azureConfigMissing ? (
          <ChatInterface
            session={currentSession}
            availableTools={availableTools}
            azureConfig={azureConfig} // Pass potentially incomplete but truthy config
            socket={socket}
          />
        ) : currentSession && azureConfigMissing ? (
          <Paper
            sx={{
              height: 'calc(100vh - 64px - 48px)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexDirection: 'column',
              p: 3,
            }}
          >
            <Typography variant="h5" gutterBottom>
              Azure OpenAI settings are required
            </Typography>
            <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
              Please configure it via the "Azure Settings" button above.
            </Typography>
            <Button
              variant="contained"
              startIcon={<SettingsIcon />}
              onClick={() => setShowAzureConfig(true)}
            >
              Open Azure Settings
            </Button>
          </Paper>
        ) : (
          <Paper
            sx={{
              height: 'calc(100vh - 64px - 48px)', 
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexDirection: 'column',
            }}
          >
            <Typography variant="h5">Start a conversation</Typography>
            <Typography variant="body1" color="text.secondary">
              Use the "New Chat" button in the top left to start a new session.
            </Typography>
          </Paper>
        )}

        {/* Dialogs */}
        {showAzureConfig && (
          <AzureConfigDialog
            open={showAzureConfig}
            onClose={() => setShowAzureConfig(false)}
            onSave={handleSaveAzureConfig}
            initialConfig={azureConfig || serverAzureConfig}
            serverConfig={serverAzureConfig}
          />
        )}        {showMCPManager && (
          <MCPServerManager
            open={showMCPManager}
            onClose={() => setShowMCPManager(false)}            servers={mcpServers}
            socket={socket} // Add socket prop
          />
        )}
        
        {error && (
          <Snackbar
            open={!!error}
            autoHideDuration={6000}
            onClose={handleCloseError}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
          >
            <Alert onClose={handleCloseError} severity="error" sx={{ width: '100%' }}>
              {error}
            </Alert>
          </Snackbar>
        )}
      </Box>
    </Box>
  );
};

export default MainLayout;

// helper to convert between camelCase and snake_case keys
const toSnake = (cfg: any) => ({
  endpoint: cfg.endpoint,
  api_key: cfg.apiKey ?? cfg.api_key,
  deployment: cfg.deployment,
  api_version: cfg.apiVersion ?? cfg.api_version,
  system_prompt: cfg.systemPrompt ?? cfg.system_prompt,
  temperature: cfg.temperature === undefined ? '' : cfg.temperature,
  top_p: cfg.topP ?? cfg.top_p ?? '',
  max_tokens: cfg.maxTokens ?? cfg.max_tokens ?? '',
});
const toCamel = (cfg: any) => ({
  endpoint: cfg.endpoint,
  apiKey: cfg.api_key ?? cfg.apiKey,
  deployment: cfg.deployment,
  apiVersion: cfg.api_version ?? cfg.apiVersion,
  systemPrompt: cfg.system_prompt ?? cfg.systemPrompt,
  temperature: cfg.temperature === '' ? undefined : cfg.temperature,
  topP: cfg.top_p === '' ? undefined : (cfg.top_p ?? cfg.topP),
  maxTokens: cfg.max_tokens === '' ? undefined : (cfg.max_tokens ?? cfg.maxTokens),
});

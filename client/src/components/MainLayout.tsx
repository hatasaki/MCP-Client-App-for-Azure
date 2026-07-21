import React, { useEffect, useState } from 'react';
import {
  Alert,
  AppBar,
  Box,
  Button,
  Drawer,
  Paper,
  Snackbar,
  Toolbar,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import SettingsIcon from '@mui/icons-material/Settings';

import { useSocket } from '../contexts/SocketContext';
import {
  ChatSession,
  FoundrySettings,
  FoundrySettingsWrite,
  MCPServerConfig,
  MCPTool,
} from '../types';
import ChatInterface from './ChatInterface';
import FoundryConfigDialog from './FoundryConfigDialog';
import MCPServerManager from './MCPServerManager';
import SessionList from './SessionList';

const DRAWER_WIDTH = 300;
const backendUrl = window.location.origin;

const MainLayout: React.FC = () => {
  const { socket, isConnected } = useSocket();
  const [currentSession, setCurrentSession] = useState<ChatSession | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServerConfig[]>([]);
  const [availableTools, setAvailableTools] = useState<MCPTool[]>([]);
  const [foundrySettings, setFoundrySettings] = useState<FoundrySettings | null>(null);
  const [settingsConfigured, setSettingsConfigured] = useState<boolean | null>(null);
  const [showMCPManager, setShowMCPManager] = useState(false);
  const [showFoundrySettings, setShowFoundrySettings] = useState(false);
  const [error, setError] = useState('');

  const loadFoundrySettings = async () => {
    try {
      const statusResponse = await fetch(`${backendUrl}/foundry-settings/status`);
      if (!statusResponse.ok) throw new Error('Unable to read Foundry settings status.');
      const status = await statusResponse.json();
      setSettingsConfigured(Boolean(status.isConfigured));
      if (!status.isConfigured) {
        setFoundrySettings(status.recoverableSettings || null);
        if (typeof status.error === 'string' && status.error) setError(status.error);
        return;
      }
      const response = await fetch(`${backendUrl}/foundry-settings`);
      if (!response.ok) throw new Error('Unable to read Microsoft Foundry settings.');
      setFoundrySettings(await response.json());
    } catch (caught) {
      setSettingsConfigured(false);
      setError(caught instanceof Error ? caught.message : 'Failed to load Microsoft Foundry settings.');
    }
  };

  useEffect(() => {
    // Remove the legacy browser copy, which could contain an API key.
    localStorage.removeItem('azureConfig');
    loadFoundrySettings();
  }, []);

  useEffect(() => {
    if (!socket) return;

    const updateSession = (session: ChatSession) => {
      setSessions((previous) => {
        const exists = previous.some((item) => item.id === session.id);
        return exists
          ? previous.map((item) => item.id === session.id ? session : item)
          : [session, ...previous];
      });
      setCurrentSession((previous) => previous?.id === session.id ? session : previous);
    };

    const terminalEvent = (event: { session?: ChatSession }) => {
      if (event.session) updateSession(event.session);
    };

    const refresh = () => {
      loadFoundrySettings();
      socket.emit('getSessions');
      socket.emit('getMCPServers');
    };

    const replaceSessions = (received: ChatSession[]) => {
      setSessions(received);
      setCurrentSession((current) => {
        if (!current) return current;
        return received.find((session) => session.id === current.id) || null;
      });
    };

    socket.on('connect', refresh);
    socket.on('sessionCreated', (session: ChatSession) => {
      setCurrentSession(session);
      setSessions((previous) => [session, ...previous.filter((item) => item.id !== session.id)]);
    });
    socket.on('sessions', replaceSessions);
    socket.on('sessionLoaded', setCurrentSession);
    socket.on('sessionUpdated', updateSession);
    socket.on('chat:completed', terminalEvent);
    socket.on('chat:cancelled', terminalEvent);
    socket.on('chat:error', terminalEvent);
    socket.on('foundrySettingsUpdated', (settings: FoundrySettings) => {
      setFoundrySettings(settings);
      setSettingsConfigured(true);
    });

    socket.on('mcpServers', (servers: MCPServerConfig[]) => {
      setMcpServers(servers);
      servers.forEach((server) => socket.emit('getMCPServerTools', server.id || server.name));
    });
    socket.on('mcpServerRegistered', (server: MCPServerConfig) => {
      setMcpServers((previous) => [
        ...previous.filter((item) => (item.id || item.name) !== (server.id || server.name)),
        server,
      ]);
      socket.emit('getMCPServerTools', server.id || server.name);
    });
    socket.on('mcpServerTools', (data: { serverId: string; tools: MCPTool[] }) => {
      setAvailableTools((previous) => [
        ...previous.filter((tool) => tool.serverId !== data.serverId),
        ...data.tools,
      ]);
    });
    socket.on('mcpServerRemoved', (serverId: string) => {
      setMcpServers((previous) => previous.filter((server) => (server.id || server.name) !== serverId));
      setAvailableTools((previous) => previous.filter((tool) => tool.serverId !== serverId));
    });
    socket.on('error', (payload: { message: string }) => setError(payload.message));

    socket.emit('getSessions');
    socket.emit('getMCPServers');

    return () => {
      socket.off('connect', refresh);
      socket.off('sessionCreated');
      socket.off('sessions', replaceSessions);
      socket.off('sessionLoaded');
      socket.off('sessionUpdated');
      socket.off('chat:completed', terminalEvent);
      socket.off('chat:cancelled', terminalEvent);
      socket.off('chat:error', terminalEvent);
      socket.off('foundrySettingsUpdated');
      socket.off('mcpServers');
      socket.off('mcpServerRegistered');
      socket.off('mcpServerTools');
      socket.off('mcpServerRemoved');
      socket.off('error');
    };
  }, [socket]);

  const saveFoundrySettings = async (settings: FoundrySettingsWrite) => {
    const response = await fetch(`${backendUrl}/foundry-settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body);
      throw new Error(detail || 'Failed to save Microsoft Foundry settings.');
    }
    setFoundrySettings(await response.json());
    setSettingsConfigured(true);
  };

  const configured = settingsConfigured === true && foundrySettings !== null;

  return (
    <Box sx={{ display: 'flex', height: '100vh' }}>
      <AppBar position="fixed" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
        <Toolbar>
          <Typography variant="h6" noWrap sx={{ flexGrow: 1 }}>
            MCP Client for Microsoft Foundry
          </Typography>
          {!isConnected && <Alert severity="error" sx={{ mr: 2 }}>Cannot connect to server</Alert>}
          <Button
            color="inherit"
            startIcon={<SettingsIcon />}
            onClick={() => setShowFoundrySettings(true)}
            sx={{ mr: 1 }}
          >
            Foundry Settings
          </Button>
          <Button color="inherit" onClick={() => setShowMCPManager(true)} sx={{ mr: 2 }}>
            MCP Servers
          </Button>
          <Button
            color="inherit"
            startIcon={<AddIcon />}
            onClick={() => socket?.emit('createNewSession')}
            disabled={!isConnected}
          >
            New Chat
          </Button>
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          [`& .MuiDrawer-paper`]: { width: DRAWER_WIDTH, boxSizing: 'border-box' },
        }}
      >
        <Toolbar />
        <Box sx={{ overflow: 'auto', p: 1 }}>
          <SessionList
            sessions={sessions}
            currentSession={currentSession}
            onSelectSession={(sessionId) => socket?.emit('loadSession', sessionId)}
            onDeleteSession={(sessionId) => {
              socket?.emit('deleteSession', sessionId);
              if (currentSession?.id === sessionId) setCurrentSession(null);
            }}
          />
        </Box>
      </Drawer>

      <Box component="main" sx={{ flexGrow: 1, p: 3 }}>
        <Toolbar />
        {currentSession && configured ? (
          <ChatInterface
            session={currentSession}
            availableTools={availableTools}
            settingsConfigured={configured}
            settings={foundrySettings}
            socket={socket}
          />
        ) : currentSession ? (
          <Paper sx={{ height: 'calc(100vh - 112px)', display: 'grid', placeItems: 'center', p: 3 }}>
            <Box sx={{ textAlign: 'center' }}>
              <Typography variant="h5" gutterBottom>Microsoft Foundry settings are required</Typography>
              <Typography color="text.secondary" sx={{ mb: 2 }}>
                Configure a Project or Model endpoint before sending messages.
              </Typography>
              <Button variant="contained" startIcon={<SettingsIcon />} onClick={() => setShowFoundrySettings(true)}>
                Open Foundry Settings
              </Button>
            </Box>
          </Paper>
        ) : (
          <Paper sx={{ height: 'calc(100vh - 112px)', display: 'grid', placeItems: 'center' }}>
            <Box sx={{ textAlign: 'center' }}>
              <Typography variant="h5">Start a conversation</Typography>
              <Typography color="text.secondary">Select New Chat to create a session.</Typography>
            </Box>
          </Paper>
        )}

        <FoundryConfigDialog
          open={showFoundrySettings}
          onClose={() => setShowFoundrySettings(false)}
          onSave={saveFoundrySettings}
          initialConfig={foundrySettings}
        />
        <MCPServerManager
          open={showMCPManager}
          onClose={() => setShowMCPManager(false)}
          servers={mcpServers}
          socket={socket}
        />
        <Snackbar open={!!error} autoHideDuration={6000} onClose={() => setError('')}>
          <Alert onClose={() => setError('')} severity="error" sx={{ width: '100%' }}>{error}</Alert>
        </Snackbar>
      </Box>
    </Box>
  );
};

export default MainLayout;

import React from 'react';
import {
  Box,
  Typography,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Badge,
} from '@mui/material';
import ChatIcon from '@mui/icons-material/Chat';
import DeleteIcon from '@mui/icons-material/Delete';

import { ChatSession } from '../types';

interface SessionListProps {
  sessions: ChatSession[];
  currentSession: ChatSession | null;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void; // add delete prop
}

const SessionList: React.FC<SessionListProps> = ({
  sessions,
  currentSession,
  onSelectSession,
  onDeleteSession, // destructure delete callback
}) => {
  const formatDate = (date: Date) => {
    const now = new Date();
    const sessionDate = new Date(date);
    const isToday = now.toDateString() === sessionDate.toDateString();
    
    if (isToday) {
      return sessionDate.toLocaleTimeString('ja-JP', {
        hour: '2-digit',
        minute: '2-digit',
      });
    }
    
    const isThisYear = now.getFullYear() === sessionDate.getFullYear();
    if (isThisYear) {
      return sessionDate.toLocaleDateString('ja-JP', {
        month: 'short',
        day: 'numeric',
      });
    }
    
    return sessionDate.toLocaleDateString('ja-JP', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const getMessageCount = (session: ChatSession) => {
    return session.messages.length;
  };

  const getLastMessage = (session: ChatSession) => {
    if (session.messages.length === 0) return 'No messages';
    
    const lastMessage = session.messages[session.messages.length - 1];
    const content = lastMessage.content;
    
    if (content.length > 50) {
      return content.substring(0, 50) + '...';
    }
    
    return content;
  };

  if (sessions.length === 0) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '200px',
          textAlign: 'center',
          color: 'text.secondary',
        }}
      >
        <ChatIcon sx={{ fontSize: 48, mb: 2, opacity: 0.5 }} />
        <Typography variant="body2">
          No chat history
        </Typography>
        <Typography variant="caption">
          Start a new chat
        </Typography>
      </Box>
    );
  }

  // Sort by newest first (updatedAt desc)
  const sortedSessions = [...sessions].sort((a, b) => {
    const tA = new Date(a.updatedAt as any).getTime();
    const tB = new Date(b.updatedAt as any).getTime();
    return tB - tA;
  });
  
  return (
    <Box>
      <Typography
        variant="h6"
        sx={{
          p: 2,
          pb: 1,
          color: 'text.secondary',
          fontSize: '0.875rem',
          fontWeight: 600,
        }}
      >
        Chat History
      </Typography>
      
      <List sx={{ pt: 0 }}>
        {sortedSessions.map((session) => (
          <ListItem
            key={session.id}
            disablePadding
            secondaryAction={
              session.autoApproveAll ? (
                <Badge
                  badgeContent="Auto Approve"
                  color="warning"
                  overlap="circular"
                  anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
                  sx={{
                    '.MuiBadge-badge': {
                      fontSize: '0.45rem',
                      lineHeight: 1.0,
                      height: 12,
                      minWidth: 12,
                      px: 1,
                      whiteSpace: 'nowrap',
                      transform: 'translate(-25%, -170%)', // align to title row, no scaling
                      transformOrigin: 'top left',
                     boxSizing: 'border-box',
                    },
                  }}
                >
                  <IconButton
                    edge="end"
                    onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                  >
                    <DeleteIcon />
                  </IconButton>
                </Badge>
              ) : (
                <IconButton
                  edge="end"
                  onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                >
                  <DeleteIcon />
                </IconButton>
              )
            }
          >
            <ListItemButton
              selected={currentSession?.id === session.id}
              onClick={() => onSelectSession(session.id)}
              sx={{ borderRadius: 1, mx: 1, mb: 0.5 }}
            >
              <ListItemText
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: currentSession?.id === session.id ? 600 : 400,
                        flexGrow: 1,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {session.name}
                    </Typography>
                  </Box>
                }
                secondary={
                  <Box sx={{ mt: 0.5 }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{
                        display: 'block',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {getLastMessage(session)}
                    </Typography>
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        mt: 0.5,
                      }}
                    >
                      <Typography variant="caption" color="text.secondary">
                        {formatDate(session.updatedAt)}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {getMessageCount(session)} msg
                      </Typography>
                    </Box>
                  </Box>
                }
                primaryTypographyProps={{ component: 'div' }}
                secondaryTypographyProps={{ component: 'div' }}
              />
            </ListItemButton>
          </ListItem>
        ))}
      </List>
    </Box>
  );
};

export default SessionList;

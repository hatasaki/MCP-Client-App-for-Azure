import React, { createContext, useContext, useEffect, useState } from 'react';
import { io, Socket } from 'socket.io-client';

interface SocketContextType {
  socket: Socket | null;
  isConnected: boolean;
}

const SocketContext = createContext<SocketContextType>({
  socket: null,
  isConnected: false,
});

export const useSocket = () => {
  const context = useContext(SocketContext);
  if (!context) {
    throw new Error('useSocket must be used within a SocketProvider');
  }
  return context;
};

export const SocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [socket, setSocket] = useState<Socket | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    // derive backend URL: Vite (VITE_BACKEND_URL) or CRA (REACT_APP_BACKEND_URL)
    const backendUrl = window.location.origin

    // Connect explicitly to backend port to avoid same-origin issue
    const newSocket = io(backendUrl, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
    });

    newSocket.on('connect', () => {
      console.log('[SocketContext] Connected to server successfully');
      setIsConnected(true);
    });

    newSocket.on('disconnect', (reason) => {
      console.log('[SocketContext] Disconnected from server, reason:', reason);
      setIsConnected(false);
    });

    newSocket.on('connect_error', (error) => {
      console.error('[SocketContext] Connection error - details not logged for security');
      setIsConnected(false);
    });

    newSocket.on('error', (error) => {
      console.error('[SocketContext] Socket error - details not logged for security');
    });

    setSocket(newSocket);

    return () => {
      newSocket.close();
    };
  }, []);

  return (
    <SocketContext.Provider value={{ socket, isConnected }}>
      {children}
    </SocketContext.Provider>
  );
};

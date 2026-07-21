import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { Socket } from 'socket.io-client';

jest.mock('./MarkdownRenderer', () => ({
  __esModule: true,
  default: ({ content }: { content: string }) => content,
}));

import ChatInterface from './ChatInterface';
import { ChatSession, FoundrySettings } from '../types';

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});

const settings: FoundrySettings = {
  schemaVersion: 4,
  endpointKind: 'model',
  endpoint: 'https://example.services.ai.azure.com',
  auth: { type: 'entra_id', apiKeyConfigured: false },
  agentInstructions: 'Test instructions',
  apiProfiles: [
    {
      apiType: 'responses',
      models: ['shared', 'reasoner'],
      versionMode: 'v1',
      options: {},
    },
    {
      apiType: 'chat_completions',
      models: ['shared'],
      versionMode: 'v1',
      options: {},
    },
  ],
  defaultSelection: { apiType: 'responses', model: 'reasoner' },
};

const session: ChatSession = {
  schemaVersion: 3,
  id: 'session-1',
  name: 'Model selection test',
  messages: [],
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  selectedModel: { apiType: 'chat_completions', model: 'shared' },
};

const makeSocket = () => {
  const handlers: Record<string, (...args: any[]) => void> = {};
  const socket = {
    on: jest.fn((event: string, handler: (...args: any[]) => void) => {
      handlers[event] = handler;
      return socket;
    }),
    off: jest.fn(() => socket),
    emit: jest.fn(() => socket),
  } as unknown as Socket;
  return { socket, handlers };
};

test('shows the session model and distinguishes duplicate names by API', async () => {
  const { socket } = makeSocket();
  render(
    <ChatInterface
      session={session}
      availableTools={[]}
      settingsConfigured
      settings={settings}
      socket={socket}
    />
  );

  const selector = screen.getByRole('combobox', { name: /^Model$/ });
  expect(selector).toHaveTextContent('shared · Chat Completions');
  expect(screen.queryByText(/Switching models rebuilds agent state/i)).not.toBeInTheDocument();
  fireEvent.mouseOver(screen.getByRole('button', { name: 'Model switching information' }));
  expect(await screen.findByText(/Switching models rebuilds agent state/i)).toBeInTheDocument();

  fireEvent.mouseDown(selector);
  expect(screen.getByRole('option', { name: 'shared · Responses' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'shared · Chat Completions' })).toBeInTheDocument();
});

test('places the model selector below the message input row', () => {
  const { socket } = makeSocket();
  render(
    <ChatInterface
      session={session}
      availableTools={[]}
      settingsConfigured
      settings={settings}
      socket={socket}
    />
  );

  const inputRow = screen.getByTestId('message-input-row');
  const modelRow = screen.getByTestId('model-selector-row');
  const modelSelector = screen.getByRole('combobox', { name: /^Model$/ });
  const infoButton = screen.getByRole('button', { name: 'Model switching information' });
  expect(inputRow.compareDocumentPosition(modelRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(inputRow).not.toContainElement(modelSelector);
  expect(modelRow).toContainElement(modelSelector);
  expect(modelRow).toContainElement(infoButton);
  expect(modelSelector.compareDocumentPosition(infoButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test('persists model changes and includes the complete selection in chat send', () => {
  const { socket } = makeSocket();
  render(
    <ChatInterface
      session={session}
      availableTools={[]}
      settingsConfigured
      settings={settings}
      socket={socket}
    />
  );

  fireEvent.mouseDown(screen.getByRole('combobox', { name: /^Model$/ }));
  fireEvent.click(screen.getByRole('option', { name: 'reasoner · Responses' }));

  expect(socket.emit).toHaveBeenCalledWith('setSessionModel', {
    sessionId: 'session-1',
    selectedModel: { apiType: 'responses', model: 'reasoner' },
  });

  fireEvent.change(screen.getByPlaceholderText('Type a message (Shift+Enter for newline)'), {
    target: { value: 'hello' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));

  const chatCall = (socket.emit as jest.Mock).mock.calls.find(([event]) => event === 'chat:send');
  expect(chatCall).toBeDefined();
  expect(chatCall[1]).toMatchObject({
    sessionId: 'session-1',
    message: 'hello',
    selectedModel: { apiType: 'responses', model: 'reasoner' },
  });
});

test('disconnect clears a lost active run so controls do not remain busy after reconnect', () => {
  const { socket, handlers } = makeSocket();
  render(
    <ChatInterface
      session={session}
      availableTools={[]}
      settingsConfigured
      settings={settings}
      socket={socket}
    />
  );
  const input = screen.getByPlaceholderText('Type a message (Shift+Enter for newline)');
  fireEvent.change(input, { target: { value: 'in flight' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(input).toBeDisabled();

  act(() => handlers.disconnect());

  expect(input).not.toBeDisabled();
  expect(screen.getByText(/Connection was interrupted/i)).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: /^Model$/ })).not.toBeDisabled();
});

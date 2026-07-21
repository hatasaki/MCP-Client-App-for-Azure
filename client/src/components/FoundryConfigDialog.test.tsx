import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import FoundryConfigDialog from './FoundryConfigDialog';
import { FoundrySettings } from '../types';

const renderDialog = () => render(
  <FoundryConfigDialog open onClose={() => undefined} onSave={() => undefined} />
);

const multiProfileSettings: FoundrySettings = {
  schemaVersion: 3,
  endpointKind: 'model',
  endpoint: 'https://example.services.ai.azure.com',
  auth: { type: 'entra_id', apiKeyConfigured: false },
  agentInstructions: 'Test instructions',
  apiProfiles: [
    {
      apiType: 'responses',
      models: ['shared', 'reasoner'],
      defaultModel: 'reasoner',
      versionMode: 'v1',
      options: { temperature: 0.25, store: false },
    },
    {
      apiType: 'chat_completions',
      models: ['shared', 'chat-fast'],
      defaultModel: 'chat-fast',
      versionMode: 'dated',
      apiVersion: '2025-04-01-preview',
      options: { temperature: 1.25, maxCompletionTokens: 256 },
    },
    {
      apiType: 'claude_messages',
      models: ['shared'],
      defaultModel: 'shared',
      versionMode: 'provider',
      options: { maxTokens: 1024, temperature: 0.4 },
    },
  ],
  defaultSelection: { apiType: 'responses', model: 'reasoner' },
};

test('project endpoint locks Entra auth and explains API key alternative', async () => {
  renderDialog();

  expect(screen.getByText(/Project endpoints use Entra ID/i)).toBeInTheDocument();
  const auth = screen.getByRole('combobox', { name: 'Authentication' });
  expect(auth).toHaveAttribute('aria-disabled', 'true');

  const info = screen.getByLabelText('Project endpoint authentication information');
  fireEvent.mouseOver(info);
  expect(await screen.findByText(/FoundryChatClient authenticates Project endpoints/i)).toBeInTheDocument();
});

test('model endpoint enables API and authentication selection', () => {
  renderDialog();

  fireEvent.mouseDown(screen.getByLabelText('Endpoint kind'));
  fireEvent.click(screen.getByRole('option', { name: 'Model endpoint' }));

  expect(screen.queryByText(/Project endpoints use Entra ID/i)).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Authentication' })).not.toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByRole('combobox', { name: 'API' })).not.toHaveAttribute('aria-disabled', 'true');
});

test('Claude displays beta warning and required max tokens', () => {
  renderDialog();
  fireEvent.mouseDown(screen.getByLabelText('Endpoint kind'));
  fireEvent.click(screen.getByRole('option', { name: 'Model endpoint' }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'API' }));
  fireEvent.click(screen.getByRole('option', { name: 'Claude Messages (MAF connector beta)' }));

  expect(screen.getByText(/MAF Anthropic connector is currently beta/i)).toBeInTheDocument();
  expect(screen.getByLabelText('max_tokens (required)')).toHaveValue(4096);
});

test('switching API loads and preserves each persisted profile independently', async () => {
  const onSave = jest.fn();
  render(
    <FoundryConfigDialog
      open
      onClose={() => undefined}
      onSave={onSave}
      initialConfig={multiProfileSettings}
    />
  );

  expect(screen.getByLabelText('temperature')).toHaveValue(0.25);
  expect(screen.getAllByLabelText('Model deployment name')[0]).toHaveValue('shared');
  expect(screen.getAllByLabelText('Model deployment name')[1]).toHaveValue('reasoner');
  fireEvent.change(screen.getByLabelText('temperature'), { target: { value: '0.5' } });

  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'API' }));
  fireEvent.click(screen.getByRole('option', { name: 'Chat Completions' }));
  expect(screen.getByLabelText('temperature')).toHaveValue(1.25);
  expect(screen.getByRole('textbox', { name: /API version/ })).toHaveValue('2025-04-01-preview');
  expect(screen.getAllByLabelText('Model deployment name')[0]).toHaveValue('shared');
  fireEvent.change(screen.getByLabelText('temperature'), { target: { value: '1.5' } });

  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'API' }));
  fireEvent.click(screen.getByRole('option', { name: 'Responses' }));
  expect(screen.getByLabelText('temperature')).toHaveValue(0.5);

  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const saved = onSave.mock.calls[0][0];
  expect(saved.schemaVersion).toBe(3);
  expect(saved.apiProfiles).toHaveLength(3);
  expect(saved.apiProfiles.find((profile: any) => profile.apiType === 'responses').options.temperature).toBe(0.5);
  expect(saved.apiProfiles.find((profile: any) => profile.apiType === 'chat_completions').options.temperature).toBe(1.5);
  expect(saved.apiProfiles.filter((profile: any) => profile.models.includes('shared'))).toHaveLength(3);
});

test('switching a dated model profile to Project normalizes Responses to v1', async () => {
  const onSave = jest.fn();
  const datedSettings: FoundrySettings = {
    ...multiProfileSettings,
    apiProfiles: [{
      apiType: 'responses',
      models: ['response-model'],
      defaultModel: 'response-model',
      versionMode: 'dated',
      apiVersion: '2025-04-01-preview',
      options: {},
    }],
    defaultSelection: { apiType: 'responses', model: 'response-model' },
  };
  render(
    <FoundryConfigDialog
      open
      onClose={() => undefined}
      onSave={onSave}
      initialConfig={datedSettings}
    />
  );

  fireEvent.mouseDown(screen.getByLabelText('Endpoint kind'));
  fireEvent.click(screen.getByRole('option', { name: 'Foundry Project endpoint' }));
  fireEvent.change(screen.getByRole('textbox', { name: /Project endpoint/ }), {
    target: { value: 'https://example.services.ai.azure.com/api/projects/demo' },
  });
  expect(screen.queryByRole('textbox', { name: /API version/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const saved = onSave.mock.calls[0][0];
  expect(saved.apiProfiles).toHaveLength(1);
  expect(saved.apiProfiles[0].versionMode).toBe('v1');
  expect(saved.apiProfiles[0].apiVersion).toBeUndefined();
});

test('unreadable encrypted key retains profiles and requires replacement', () => {
  const recoverySettings: FoundrySettings = {
    ...multiProfileSettings,
    auth: {
      type: 'api_key',
      apiKeyConfigured: false,
      apiKeyNeedsReplacement: true,
    },
  };
  render(
    <FoundryConfigDialog
      open
      onClose={() => undefined}
      onSave={() => undefined}
      initialConfig={recoverySettings}
    />
  );

  expect(screen.getByText(/encrypted API key cannot be read/i)).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'API key action' })).toHaveTextContent('Set key');
  expect(screen.getByLabelText(/API key/, { selector: 'input' })).toHaveValue('');
  expect(screen.getAllByLabelText('Model deployment name')[0]).toHaveValue('shared');
  expect(screen.getAllByLabelText('Model deployment name')[1]).toHaveValue('reasoner');
});

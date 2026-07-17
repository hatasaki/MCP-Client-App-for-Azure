import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';

import KeyValueRows, { KeyValueRow, recordToRows, rowsToRecord } from './KeyValueRows';
import {
  ApiType,
  AuthType,
  EndpointKind,
  FoundrySettings,
  FoundrySettingsWrite,
  SecretAction,
  VersionMode,
} from '../types';

const DEFAULT_INSTRUCTIONS =
  "Based on the user's instructions, analyze the user's intent, define goals to achieve that intent, invoke and execute necessary tools until the goals are accomplished, and finally return the response to the user.";

interface FoundryConfigDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (config: FoundrySettingsWrite) => Promise<void> | void;
  initialConfig?: FoundrySettings | null;
}

interface FormState {
  endpointKind: EndpointKind;
  endpoint: string;
  model: string;
  apiType: ApiType;
  versionMode: VersionMode;
  apiVersion: string;
  authType: AuthType;
  apiKeyConfigured: boolean;
  secretAction: SecretAction;
  apiKeyValue: string;
  agentInstructions: string;
  options: Record<string, any>;
}

const defaultForm = (): FormState => ({
  endpointKind: 'project',
  endpoint: '',
  model: '',
  apiType: 'responses',
  versionMode: 'v1',
  apiVersion: '',
  authType: 'entra_id',
  apiKeyConfigured: false,
  secretAction: 'keep',
  apiKeyValue: '',
  agentInstructions: DEFAULT_INSTRUCTIONS,
  options: {},
});

const FoundryConfigDialog: React.FC<FoundryConfigDialogProps> = ({
  open,
  onClose,
  onSave,
  initialConfig,
}) => {
  const [form, setForm] = useState<FormState>(defaultForm);
  const [metadataRows, setMetadataRows] = useState<KeyValueRow[]>(recordToRows());
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (initialConfig) {
      const options = { ...(initialConfig.options as Record<string, any>) };
      if (Array.isArray(options.stop)) options.stop = options.stop.join(', ');
      if (Array.isArray(options.stopSequences)) options.stopSequences = options.stopSequences.join(', ');
      if (options.thinking) {
        options.thinkingType = options.thinking.type;
        if (options.thinking.type === 'enabled') {
          options.thinkingBudgetTokens = options.thinking.budgetTokens;
        }
        delete options.thinking;
      }
      setForm({
        endpointKind: initialConfig.endpointKind,
        endpoint: initialConfig.endpoint,
        model: initialConfig.model,
        apiType: initialConfig.apiType,
        versionMode: initialConfig.versionMode,
        apiVersion: initialConfig.apiVersion || '',
        authType: initialConfig.auth.type,
        apiKeyConfigured: initialConfig.auth.apiKeyConfigured,
        secretAction: 'keep',
        apiKeyValue: '',
        agentInstructions: initialConfig.agentInstructions,
        options,
      });
      setMetadataRows(recordToRows(options.metadata));
    } else {
      setForm(defaultForm());
      setMetadataRows(recordToRows());
    }
    setFormError('');
  }, [open, initialConfig]);

  const setBase = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const setOption = (key: string, value: unknown) => {
    setForm((current) => {
      const options = { ...current.options };
      if (value === '' || value === undefined || value === null) delete options[key];
      else options[key] = value;
      return { ...current, options };
    });
  };

  const changeEndpointKind = (kind: EndpointKind) => {
    if (kind === 'project') {
      setForm((current) => ({
        ...current,
        endpointKind: kind,
        apiType: 'responses',
        versionMode: 'v1',
        apiVersion: '',
        authType: 'entra_id',
        secretAction: 'clear',
        apiKeyValue: '',
        options: current.apiType === 'responses' ? current.options : {},
      }));
    } else {
      setForm((current) => ({ ...current, endpointKind: kind }));
    }
  };

  const changeApiType = (apiType: ApiType) => {
    setForm((current) => ({
      ...current,
      apiType,
      versionMode: apiType === 'claude_messages' ? 'provider' : 'v1',
      apiVersion: '',
      options: apiType === 'claude_messages' ? { maxTokens: 4096 } : {},
    }));
    setMetadataRows(recordToRows());
  };

  const changeAuthType = (authType: AuthType) => {
    setForm((current) => ({
      ...current,
      authType,
      secretAction:
        authType === 'api_key' ? (current.apiKeyConfigured ? 'keep' : 'set') : 'clear',
      apiKeyValue: '',
    }));
  };

  const validateRows = (rows: KeyValueRow[]): string | null => {
    const active = rows.filter((row) => row.key || row.value);
    if (active.some((row) => !row.key.trim())) return 'Metadata keys are required.';
    const keys = active.map((row) => row.key.trim());
    if (new Set(keys).size !== keys.length) return 'Metadata keys must be unique.';
    return null;
  };

  const handleSave = async () => {
    if (!form.endpoint.trim() || !form.model.trim()) {
      setFormError('Endpoint and model deployment name are required.');
      return;
    }
    if (form.versionMode === 'dated' && !form.apiVersion.trim()) {
      setFormError('A dated API version is required.');
      return;
    }
    if (form.authType === 'api_key' && form.secretAction === 'set' && !form.apiKeyValue.trim()) {
      setFormError('Enter an API key or choose Entra ID authentication.');
      return;
    }
    if (form.apiType === 'claude_messages' && !(Number(form.options.maxTokens) > 0)) {
      setFormError('Claude max_tokens is required and must be greater than zero.');
      return;
    }
    const rowError = validateRows(metadataRows);
    if (rowError) {
      setFormError(rowError);
      return;
    }

    const options = { ...form.options };
    if (form.apiType === 'chat_completions' && typeof options.stop === 'string') {
      const stop = options.stop.split(',').map((item: string) => item.trim()).filter(Boolean);
      if (stop.length) options.stop = stop;
      else delete options.stop;
    }
    if (form.apiType === 'claude_messages') {
      if (typeof options.stopSequences === 'string') {
        const stopSequences = options.stopSequences
          .split(',')
          .map((item: string) => item.trim())
          .filter(Boolean);
        if (stopSequences.length) options.stopSequences = stopSequences;
        else delete options.stopSequences;
      }
      const thinkingType = options.thinkingType;
      const thinkingBudgetTokens = options.thinkingBudgetTokens;
      delete options.thinkingType;
      delete options.thinkingBudgetTokens;
      if (thinkingType === 'enabled') {
        options.thinking = { type: 'enabled', budgetTokens: thinkingBudgetTokens };
      } else if (thinkingType === 'disabled' || thinkingType === 'adaptive') {
        options.thinking = { type: thinkingType };
      }
    }
    const metadata = rowsToRecord(metadataRows);
    if (Object.keys(metadata).length) options.metadata = metadata;
    else delete options.metadata;

    const payload: FoundrySettingsWrite = {
      schemaVersion: 2,
      endpointKind: form.endpointKind,
      endpoint: form.endpoint.trim(),
      model: form.model.trim(),
      apiType: form.apiType,
      versionMode: form.versionMode,
      ...(form.versionMode === 'dated' ? { apiVersion: form.apiVersion.trim() } : {}),
      auth: {
        type: form.authType,
        apiKey: {
          action: form.authType === 'entra_id' ? 'clear' : form.secretAction,
          ...(form.secretAction === 'set' ? { value: form.apiKeyValue } : {}),
        },
      },
      agentInstructions: form.agentInstructions,
      options,
    };
    setSaving(true);
    setFormError('');
    try {
      await onSave(payload);
      onClose();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Failed to save settings.');
    } finally {
      setSaving(false);
    }
  };

  const numberField = (
    label: string,
    key: string,
    constraints: { min?: number; max?: number; step?: number } = {}
  ) => (
    <TextField
      label={label}
      type="number"
      value={form.options[key] ?? ''}
      onChange={(event) => setOption(key, event.target.value === '' ? undefined : Number(event.target.value))}
      inputProps={constraints}
      helperText="Leave empty to omit."
      fullWidth
    />
  );

  const textField = (label: string, key: string, helper = 'Leave empty to omit.') => (
    <TextField
      label={label}
      value={form.options[key] ?? ''}
      onChange={(event) => setOption(key, event.target.value || undefined)}
      helperText={helper}
      fullWidth
    />
  );

  const enumField = (label: string, key: string, values: string[], includeNoneValue = false) => (
    <TextField
      select
      label={label}
      value={form.options[key] ?? ''}
      onChange={(event) => setOption(key, event.target.value || undefined)}
      helperText="Omit uses the service/model default."
      fullWidth
    >
      <MenuItem value="">Omit</MenuItem>
      {includeNoneValue && <MenuItem value="none">none</MenuItem>}
      {values.filter((value) => value !== 'none').map((value) => (
        <MenuItem key={value} value={value}>{value}</MenuItem>
      ))}
    </TextField>
  );

  const booleanField = (label: string, key: string) => (
    <TextField
      select
      label={label}
      value={form.options[key] === undefined ? '' : String(form.options[key])}
      onChange={(event) => setOption(
        key,
        event.target.value === '' ? undefined : event.target.value === 'true'
      )}
      helperText="Omit, true, and false are distinct values."
      fullWidth
    >
      <MenuItem value="">Omit</MenuItem>
      <MenuItem value="true">true</MenuItem>
      <MenuItem value="false">false</MenuItem>
    </TextField>
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Microsoft Foundry Settings</DialogTitle>
      <DialogContent sx={{ userSelect: 'text' }}>
        {formError && <Alert severity="error" sx={{ mb: 2 }}>{formError}</Alert>}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          <TextField
            select
            label="Endpoint kind"
            value={form.endpointKind}
            onChange={(event) => changeEndpointKind(event.target.value as EndpointKind)}
          >
            <MenuItem value="project">Foundry Project endpoint</MenuItem>
            <MenuItem value="model">Model endpoint</MenuItem>
          </TextField>

          {form.endpointKind === 'project' && (
            <Alert
              severity="info"
              icon={
                <Tooltip title="MAF FoundryChatClient authenticates Project endpoints with an Entra credential.">
                  <IconButton size="small" aria-label="Project endpoint authentication information">
                    <InfoOutlinedIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              }
            >
              Project endpoints use Entra ID with MAF <code>FoundryChatClient</code>. To use a resource API key,
              select <strong>Model endpoint</strong>. Model endpoints do not expose Project-scoped connections or
              platform capabilities.
            </Alert>
          )}

          <TextField
            label={form.endpointKind === 'project' ? 'Project endpoint' : 'Model resource endpoint'}
            value={form.endpoint}
            onChange={(event) => setBase('endpoint', event.target.value)}
            placeholder={
              form.endpointKind === 'project'
                ? 'https://resource.services.ai.azure.com/api/projects/project-name'
                : 'https://resource.services.ai.azure.com'
            }
            required
          />
          <TextField
            label="Model deployment name"
            value={form.model}
            onChange={(event) => setBase('model', event.target.value)}
            required
          />

          <FormControl fullWidth>
            <InputLabel id="foundry-api-label">API</InputLabel>
            <Select
              id="foundry-api"
              labelId="foundry-api-label"
              label="API"
              value={form.apiType}
              disabled={form.endpointKind === 'project'}
              onChange={(event) => changeApiType(event.target.value as ApiType)}
            >
              <MenuItem value="responses">Responses</MenuItem>
              <MenuItem value="chat_completions">Chat Completions</MenuItem>
              <MenuItem value="claude_messages">Claude Messages (MAF connector beta)</MenuItem>
            </Select>
          </FormControl>

          {form.endpointKind === 'model' && form.apiType !== 'claude_messages' && (
            <TextField
              select
              label="API version mode"
              value={form.versionMode}
              onChange={(event) => {
                const value = event.target.value as VersionMode;
                setBase('versionMode', value);
                if (value === 'v1') setBase('apiVersion', '');
              }}
            >
              <MenuItem value="v1">v1</MenuItem>
              <MenuItem value="dated">Dated API version</MenuItem>
            </TextField>
          )}
          {form.versionMode === 'dated' && (
            <TextField
              label="API version"
              value={form.apiVersion}
              onChange={(event) => setBase('apiVersion', event.target.value)}
              placeholder="2025-04-01-preview"
              required
            />
          )}

          <FormControl fullWidth>
            <InputLabel id="foundry-auth-label">Authentication</InputLabel>
            <Select
              id="foundry-auth"
              labelId="foundry-auth-label"
              label="Authentication"
              value={form.authType}
              disabled={form.endpointKind === 'project'}
              onChange={(event) => changeAuthType(event.target.value as AuthType)}
            >
              <MenuItem value="entra_id">Microsoft Entra ID</MenuItem>
              <MenuItem value="api_key">API key</MenuItem>
            </Select>
          </FormControl>

          {form.authType === 'api_key' && (
            <>
              <TextField
                select
                label="API key action"
                value={form.secretAction}
                onChange={(event) => {
                  const action = event.target.value as SecretAction;
                  if (action === 'clear') {
                    setForm((current) => ({
                      ...current,
                      authType: 'entra_id',
                      secretAction: 'clear',
                      apiKeyValue: '',
                    }));
                  } else {
                    setBase('secretAction', action);
                  }
                }}
              >
                {form.apiKeyConfigured && <MenuItem value="keep">Keep configured key</MenuItem>}
                <MenuItem value="set">{form.apiKeyConfigured ? 'Replace key' : 'Set key'}</MenuItem>
                {form.apiKeyConfigured && <MenuItem value="clear">Clear key and use Entra ID</MenuItem>}
              </TextField>
              {form.secretAction === 'set' && (
                <TextField
                  label="API key"
                  type="password"
                  value={form.apiKeyValue}
                  onChange={(event) => setBase('apiKeyValue', event.target.value)}
                  autoComplete="new-password"
                  required
                />
              )}
            </>
          )}

          <TextField
            label="Agent Instructions"
            value={form.agentInstructions}
            onChange={(event) => setBase('agentInstructions', event.target.value)}
            multiline
            minRows={4}
          />

          <Divider />
          <Typography variant="h6">{form.apiType === 'responses' ? 'Responses' : form.apiType === 'chat_completions' ? 'Chat Completions' : 'Claude Messages'} parameters</Typography>

          {form.apiType === 'responses' && (
            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 2 }}>
              {numberField('temperature', 'temperature', { min: 0, max: 2, step: 0.1 })}
              {numberField('top_p', 'topP', { min: 0, max: 1, step: 0.05 })}
              {numberField('max_output_tokens', 'maxOutputTokens', { min: 1 })}
              {enumField('reasoning.effort', 'reasoningEffort', ['minimal', 'low', 'medium', 'high', 'xhigh'], true)}
              {enumField('reasoning.summary', 'reasoningSummary', ['auto', 'concise', 'detailed'])}
              {enumField('verbosity', 'verbosity', ['low', 'medium', 'high'])}
              {booleanField('store', 'store')}
              {booleanField('parallel_tool_calls', 'parallelToolCalls')}
              {enumField('service_tier', 'serviceTier', ['auto', 'default', 'flex', 'priority'])}
              {enumField('truncation', 'truncation', ['auto', 'disabled'])}
              {numberField('max_tool_calls', 'maxToolCalls', { min: 1 })}
              {textField('safety_identifier', 'safetyIdentifier', 'Maximum 64 characters; empty = Omit.')}
              {textField('prompt_cache_key', 'promptCacheKey')}
            </Box>
          )}

          {form.apiType === 'chat_completions' && (
            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 2 }}>
              {numberField('temperature', 'temperature', { min: 0, max: 2, step: 0.1 })}
              {numberField('top_p', 'topP', { min: 0, max: 1, step: 0.05 })}
              {numberField('max_completion_tokens', 'maxCompletionTokens', { min: 1 })}
              {enumField('reasoning_effort', 'reasoningEffort', ['minimal', 'low', 'medium', 'high', 'xhigh'], true)}
              {enumField('verbosity', 'verbosity', ['low', 'medium', 'high'])}
              {textField('stop sequences', 'stop', 'Comma-separated sequences; empty = Omit.')}
              {numberField('seed', 'seed')}
              {numberField('frequency_penalty', 'frequencyPenalty', { min: -2, max: 2, step: 0.1 })}
              {numberField('presence_penalty', 'presencePenalty', { min: -2, max: 2, step: 0.1 })}
              {booleanField('logprobs', 'logprobs')}
              {numberField('top_logprobs', 'topLogprobs', { min: 0, max: 20 })}
              {booleanField('store', 'store')}
              {booleanField('parallel_tool_calls', 'parallelToolCalls')}
              {enumField('service_tier', 'serviceTier', ['auto', 'default', 'flex', 'priority'])}
              {textField('safety_identifier', 'safetyIdentifier', 'Maximum 64 characters; empty = Omit.')}
              {textField('prompt_cache_key', 'promptCacheKey')}
            </Box>
          )}

          {form.apiType === 'claude_messages' && (
            <>
              <Alert severity="warning">
                The MAF Anthropic connector is currently beta. Claude <code>max_tokens</code> is required and cannot be omitted.
              </Alert>
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 2 }}>
                {numberField('max_tokens (required)', 'maxTokens', { min: 1 })}
                {numberField('temperature', 'temperature', { min: 0, max: 1, step: 0.1 })}
                {numberField('top_p', 'topP', { min: 0, max: 1, step: 0.05 })}
                {numberField('top_k', 'topK', { min: 1 })}
                {textField('stop_sequences', 'stopSequences', 'Comma-separated sequences; empty = Omit.')}
                {enumField('output effort', 'effort', ['low', 'medium', 'high', 'max'])}
                {enumField('service_tier', 'serviceTier', ['auto', 'standard_only'])}
                {booleanField('parallel tool use', 'parallelToolUse')}
                {textField('metadata.user_id', 'metadataUserId')}
                {enumField('thinking.type', 'thinkingType', ['disabled', 'enabled', 'adaptive'])}
                {form.options.thinkingType === 'enabled' && numberField('thinking.budget_tokens', 'thinkingBudgetTokens', { min: 1 })}
              </Box>
            </>
          )}

          {form.apiType !== 'claude_messages' && (
            <>
              <Typography variant="subtitle1">Metadata</Typography>
              <KeyValueRows rows={metadataRows} onChange={setMetadataRows} />
            </>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button onClick={handleSave} variant="contained" disabled={saving}>Save</Button>
      </DialogActions>
    </Dialog>
  );
};

export default FoundryConfigDialog;

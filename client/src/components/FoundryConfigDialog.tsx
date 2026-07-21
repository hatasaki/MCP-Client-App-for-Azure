import React, { useEffect, useMemo, useState } from 'react';
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

import DeploymentRows, { deploymentsToRows, rowsToDeployments } from './DeploymentRows';
import KeyValueRows, { KeyValueRow, recordToRows, rowsToRecord } from './KeyValueRows';
import {
  ApiProfile,
  ApiType,
  AuthType,
  EndpointKind,
  FoundrySettings,
  FoundrySettingsWrite,
  ModelSelection,
  SecretAction,
  VersionMode,
} from '../types';

const DEFAULT_INSTRUCTIONS =
  "Based on the user's instructions, analyze the user's intent, define goals to achieve that intent, invoke and execute necessary tools until the goals are accomplished, and finally return the response to the user.";
const API_TYPES: ApiType[] = ['responses', 'chat_completions', 'claude_messages'];
const API_LABELS: Record<ApiType, string> = {
  responses: 'Responses',
  chat_completions: 'Chat Completions',
  claude_messages: 'Claude Messages',
};

interface FoundryConfigDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (config: FoundrySettingsWrite) => Promise<void> | void;
  initialConfig?: FoundrySettings | null;
}

interface CommonFormState {
  endpointKind: EndpointKind;
  endpoint: string;
  authType: AuthType;
  apiKeyConfigured: boolean;
  secretAction: SecretAction;
  apiKeyValue: string;
  agentInstructions: string;
}

interface ProfileFormState {
  apiType: ApiType;
  modelRows: string[];
  defaultModel: string;
  versionMode: VersionMode;
  apiVersion: string;
  options: Record<string, any>;
  metadataRows: KeyValueRow[];
}

type ProfileForms = Record<ApiType, ProfileFormState>;

const defaultCommon = (): CommonFormState => ({
  endpointKind: 'project',
  endpoint: '',
  authType: 'entra_id',
  apiKeyConfigured: false,
  secretAction: 'keep',
  apiKeyValue: '',
  agentInstructions: DEFAULT_INSTRUCTIONS,
});

const defaultProfile = (apiType: ApiType): ProfileFormState => ({
  apiType,
  modelRows: deploymentsToRows(),
  defaultModel: '',
  versionMode: apiType === 'claude_messages' ? 'provider' : 'v1',
  apiVersion: '',
  options: apiType === 'claude_messages' ? { maxTokens: 4096 } : {},
  metadataRows: recordToRows(),
});

const normalizeOptionsForForm = (profile: ApiProfile): Record<string, any> => {
  const options = { ...(profile.options as Record<string, any>) };
  if (Array.isArray(options.stop)) options.stop = options.stop.join(', ');
  if (Array.isArray(options.stopSequences)) options.stopSequences = options.stopSequences.join(', ');
  if (options.thinking) {
    options.thinkingType = options.thinking.type;
    if (options.thinking.type === 'enabled') {
      options.thinkingBudgetTokens = options.thinking.budgetTokens;
    }
    delete options.thinking;
  }
  return options;
};

const hydrateProfiles = (initial?: FoundrySettings | null): ProfileForms => Object.fromEntries(
  API_TYPES.map((apiType) => {
    const persisted = initial?.apiProfiles.find((profile) => profile.apiType === apiType);
    if (!persisted) return [apiType, defaultProfile(apiType)];
    const options = normalizeOptionsForForm(persisted);
    return [apiType, {
      apiType,
      modelRows: deploymentsToRows(persisted.models),
      defaultModel: persisted.defaultModel,
      versionMode: persisted.versionMode,
      apiVersion: persisted.apiVersion || '',
      options,
      metadataRows: recordToRows(options.metadata),
    } satisfies ProfileFormState];
  })
) as ProfileForms;

const selectionKey = (selection: ModelSelection): string => JSON.stringify([selection.apiType, selection.model]);
const parseSelectionKey = (value: string): ModelSelection => {
  const [apiType, model] = JSON.parse(value) as [ApiType, string];
  return { apiType, model };
};

const FoundryConfigDialog: React.FC<FoundryConfigDialogProps> = ({
  open,
  onClose,
  onSave,
  initialConfig,
}) => {
  const [common, setCommon] = useState<CommonFormState>(defaultCommon);
  const [profiles, setProfiles] = useState<ProfileForms>(() => hydrateProfiles());
  const [activeApiType, setActiveApiType] = useState<ApiType>('responses');
  const [defaultModelKey, setDefaultModelKey] = useState('');
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (initialConfig) {
      setCommon({
        endpointKind: initialConfig.endpointKind,
        endpoint: initialConfig.endpoint,
        authType: initialConfig.auth.type,
        apiKeyConfigured: initialConfig.auth.apiKeyConfigured,
        secretAction: initialConfig.auth.apiKeyConfigured
          ? 'keep'
          : initialConfig.auth.type === 'api_key' ? 'set' : 'clear',
        apiKeyValue: '',
        agentInstructions: initialConfig.agentInstructions,
      });
      setProfiles(hydrateProfiles(initialConfig));
      setActiveApiType(initialConfig.defaultSelection.apiType);
      setDefaultModelKey(selectionKey(initialConfig.defaultSelection));
    } else {
      setCommon(defaultCommon());
      setProfiles(hydrateProfiles());
      setActiveApiType('responses');
      setDefaultModelKey('');
    }
    setFormError('');
  }, [open, initialConfig]);

  const activeProfile = profiles[activeApiType];
  const activeModels = rowsToDeployments(activeProfile.modelRows);

  const availableSelections = useMemo(() => API_TYPES.flatMap((apiType) => {
    if (common.endpointKind === 'project' && apiType !== 'responses') return [];
    return rowsToDeployments(profiles[apiType].modelRows).map((model) => ({ apiType, model }));
  }), [common.endpointKind, profiles]);

  const setCommonField = <K extends keyof CommonFormState>(key: K, value: CommonFormState[K]) => {
    setCommon((current) => ({ ...current, [key]: value }));
  };

  const updateActiveProfile = (updates: Partial<ProfileFormState>) => {
    setProfiles((current) => ({
      ...current,
      [activeApiType]: { ...current[activeApiType], ...updates },
    }));
  };

  const setOption = (key: string, value: unknown) => {
    const options = { ...activeProfile.options };
    if (value === '' || value === undefined || value === null) delete options[key];
    else options[key] = value;
    updateActiveProfile({ options });
  };

  const changeEndpointKind = (kind: EndpointKind) => {
    setCommon((current) => ({
      ...current,
      endpointKind: kind,
      ...(kind === 'project' ? {
        authType: 'entra_id' as const,
        secretAction: 'clear' as const,
        apiKeyValue: '',
      } : {}),
    }));
    if (kind === 'project') {
      setProfiles((current) => ({
        ...current,
        responses: {
          ...current.responses,
          versionMode: 'v1',
          apiVersion: '',
        },
      }));
      setActiveApiType('responses');
      const responseModels = rowsToDeployments(profiles.responses.modelRows);
      if (responseModels.length) {
        setDefaultModelKey(selectionKey({ apiType: 'responses', model: profiles.responses.defaultModel || responseModels[0] }));
      }
    }
  };

  const changeAuthType = (authType: AuthType) => {
    setCommon((current) => ({
      ...current,
      authType,
      secretAction: authType === 'api_key' ? (current.apiKeyConfigured ? 'keep' : 'set') : 'clear',
      apiKeyValue: '',
    }));
  };

  const validateRows = (rows: KeyValueRow[], apiType: ApiType): string | null => {
    const active = rows.filter((row) => row.key || row.value);
    if (active.some((row) => !row.key.trim())) return `${apiType}: metadata keys are required.`;
    const keys = active.map((row) => row.key.trim());
    if (new Set(keys).size !== keys.length) return `${apiType}: metadata keys must be unique.`;
    return null;
  };

  const prepareOptions = (profile: ProfileFormState): Record<string, unknown> => {
    const options = { ...profile.options };
    if (profile.apiType === 'chat_completions' && typeof options.stop === 'string') {
      const stop = options.stop.split(',').map((item: string) => item.trim()).filter(Boolean);
      if (stop.length) options.stop = stop;
      else delete options.stop;
    }
    if (profile.apiType === 'claude_messages') {
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
      if (thinkingType === 'enabled') options.thinking = { type: 'enabled', budgetTokens: thinkingBudgetTokens };
      else if (thinkingType === 'disabled' || thinkingType === 'adaptive') options.thinking = { type: thinkingType };
    }
    const metadata = rowsToRecord(profile.metadataRows);
    if (Object.keys(metadata).length) options.metadata = metadata;
    else delete options.metadata;
    return options;
  };

  const handleSave = async () => {
    if (!common.endpoint.trim()) {
      setFormError('Endpoint is required.');
      return;
    }
    if (common.authType === 'api_key' && common.secretAction === 'set' && !common.apiKeyValue.trim()) {
      setFormError('Enter an API key or choose Entra ID authentication.');
      return;
    }

    const enabledApiTypes = API_TYPES.filter((apiType) => (
      common.endpointKind !== 'project' || apiType === 'responses'
    ) && rowsToDeployments(profiles[apiType].modelRows).length > 0);
    if (!enabledApiTypes.length) {
      setFormError('Configure at least one model deployment.');
      return;
    }

    const apiProfiles: ApiProfile[] = [];
    for (const apiType of enabledApiTypes) {
      const profile = profiles[apiType];
      const models = rowsToDeployments(profile.modelRows);
      if (models.length !== new Set(models).size) {
        setFormError(`${apiType}: model deployment names must be unique.`);
        return;
      }
      const defaultModel = models.includes(profile.defaultModel) ? profile.defaultModel : models[0];
      if (profile.versionMode === 'dated' && !profile.apiVersion.trim()) {
        setFormError(`${apiType}: a dated API version is required.`);
        return;
      }
      if (apiType === 'claude_messages' && !(Number(profile.options.maxTokens) > 0)) {
        setFormError('Claude max_tokens is required and must be greater than zero.');
        return;
      }
      const rowError = validateRows(profile.metadataRows, apiType);
      if (rowError) {
        setFormError(rowError);
        return;
      }
      apiProfiles.push({
        apiType,
        models,
        defaultModel,
        versionMode: profile.versionMode,
        ...(profile.versionMode === 'dated' ? { apiVersion: profile.apiVersion.trim() } : {}),
        options: prepareOptions(profile),
      } as ApiProfile);
    }

    const selections = apiProfiles.flatMap((profile) => profile.models.map((model) => ({ apiType: profile.apiType, model })));
    const configuredKeys = new Set(selections.map(selectionKey));
    const defaultSelection = defaultModelKey && configuredKeys.has(defaultModelKey)
      ? parseSelectionKey(defaultModelKey)
      : selections[0];

    const payload: FoundrySettingsWrite = {
      schemaVersion: 3,
      endpointKind: common.endpointKind,
      endpoint: common.endpoint.trim(),
      auth: {
        type: common.authType,
        apiKey: {
          action: common.authType === 'entra_id' ? 'clear' : common.secretAction,
          ...(common.secretAction === 'set' ? { value: common.apiKeyValue } : {}),
        },
      },
      agentInstructions: common.agentInstructions,
      apiProfiles,
      defaultSelection,
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
      value={activeProfile.options[key] ?? ''}
      onChange={(event) => setOption(key, event.target.value === '' ? undefined : Number(event.target.value))}
      inputProps={constraints}
      helperText="Leave empty to omit."
      fullWidth
    />
  );

  const textField = (label: string, key: string, helper = 'Leave empty to omit.') => (
    <TextField
      label={label}
      value={activeProfile.options[key] ?? ''}
      onChange={(event) => setOption(key, event.target.value || undefined)}
      helperText={helper}
      fullWidth
    />
  );

  const enumField = (label: string, key: string, values: string[], includeNoneValue = false) => (
    <TextField
      select
      label={label}
      value={activeProfile.options[key] ?? ''}
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
      value={activeProfile.options[key] === undefined ? '' : String(activeProfile.options[key])}
      onChange={(event) => setOption(key, event.target.value === '' ? undefined : event.target.value === 'true')}
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
            value={common.endpointKind}
            onChange={(event) => changeEndpointKind(event.target.value as EndpointKind)}
          >
            <MenuItem value="project">Foundry Project endpoint</MenuItem>
            <MenuItem value="model">Model endpoint</MenuItem>
          </TextField>

          {common.endpointKind === 'project' && (
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
              Project endpoints use Entra ID and Responses API only. Multiple Responses model deployments can be configured.
              To use a resource API key, select <strong>Model endpoint</strong>.
            </Alert>
          )}

          <TextField
            label={common.endpointKind === 'project' ? 'Project endpoint' : 'Model resource endpoint'}
            value={common.endpoint}
            onChange={(event) => setCommonField('endpoint', event.target.value)}
            placeholder={
              common.endpointKind === 'project'
                ? 'https://resource.services.ai.azure.com/api/projects/project-name'
                : 'https://resource.services.ai.azure.com'
            }
            required
          />

          <FormControl fullWidth>
            <InputLabel id="foundry-api-label">API</InputLabel>
            <Select
              id="foundry-api"
              labelId="foundry-api-label"
              label="API"
              value={activeApiType}
              disabled={common.endpointKind === 'project'}
              onChange={(event) => setActiveApiType(event.target.value as ApiType)}
            >
              <MenuItem value="responses">Responses</MenuItem>
              <MenuItem value="chat_completions">Chat Completions</MenuItem>
              <MenuItem value="claude_messages">Claude Messages (MAF connector beta)</MenuItem>
            </Select>
          </FormControl>

          <Typography variant="subtitle1">Model deployments for {API_LABELS[activeApiType]}</Typography>
          <DeploymentRows
            rows={activeProfile.modelRows}
            onChange={(modelRows) => {
              const models = rowsToDeployments(modelRows);
              updateActiveProfile({
                modelRows,
                defaultModel: models.includes(activeProfile.defaultModel) ? activeProfile.defaultModel : (models[0] || ''),
              });
            }}
          />
          <TextField
            select
            label="Default model for this API"
            value={activeModels.includes(activeProfile.defaultModel) ? activeProfile.defaultModel : (activeModels[0] || '')}
            onChange={(event) => updateActiveProfile({ defaultModel: event.target.value })}
            disabled={!activeModels.length}
            helperText="Used when this API profile is selected as the default for a new chat."
          >
            {activeModels.map((model) => <MenuItem key={model} value={model}>{model}</MenuItem>)}
          </TextField>

          {common.endpointKind === 'model' && activeApiType !== 'claude_messages' && (
            <TextField
              select
              label="API version mode"
              value={activeProfile.versionMode}
              onChange={(event) => {
                const versionMode = event.target.value as VersionMode;
                updateActiveProfile({ versionMode, ...(versionMode === 'v1' ? { apiVersion: '' } : {}) });
              }}
            >
              <MenuItem value="v1">v1</MenuItem>
              <MenuItem value="dated">Dated API version</MenuItem>
            </TextField>
          )}
          {activeProfile.versionMode === 'dated' && (
            <TextField
              label="API version"
              value={activeProfile.apiVersion}
              onChange={(event) => updateActiveProfile({ apiVersion: event.target.value })}
              placeholder="2025-04-01-preview"
              required
            />
          )}

          <Divider />
          <Typography variant="h6">
            {activeApiType === 'responses' ? 'Responses' : activeApiType === 'chat_completions' ? 'Chat Completions' : 'Claude Messages'} parameters
          </Typography>

          {activeApiType === 'responses' && (
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

          {activeApiType === 'chat_completions' && (
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

          {activeApiType === 'claude_messages' && (
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
                {activeProfile.options.thinkingType === 'enabled' && numberField('thinking.budget_tokens', 'thinkingBudgetTokens', { min: 1 })}
              </Box>
            </>
          )}

          {activeApiType !== 'claude_messages' && (
            <>
              <Typography variant="subtitle1">Metadata</Typography>
              <KeyValueRows
                rows={activeProfile.metadataRows}
                onChange={(metadataRows) => updateActiveProfile({ metadataRows })}
              />
            </>
          )}

          <Divider />
          <TextField
            select
            label="Default model for new chats"
            value={availableSelections.some((item) => selectionKey(item) === defaultModelKey)
              ? defaultModelKey
              : (availableSelections[0] ? selectionKey(availableSelections[0]) : '')}
            onChange={(event) => setDefaultModelKey(event.target.value)}
            disabled={!availableSelections.length}
          >
            {availableSelections.map((selection) => (
              <MenuItem key={selectionKey(selection)} value={selectionKey(selection)}>
                {selection.model} · {API_LABELS[selection.apiType]}
              </MenuItem>
            ))}
          </TextField>

          <FormControl fullWidth>
            <InputLabel id="foundry-auth-label">Authentication</InputLabel>
            <Select
              id="foundry-auth"
              labelId="foundry-auth-label"
              label="Authentication"
              value={common.authType}
              disabled={common.endpointKind === 'project'}
              onChange={(event) => changeAuthType(event.target.value as AuthType)}
            >
              <MenuItem value="entra_id">Microsoft Entra ID</MenuItem>
              <MenuItem value="api_key">API key</MenuItem>
            </Select>
          </FormControl>

          {common.authType === 'api_key' && (
            <>
              {initialConfig?.auth.apiKeyNeedsReplacement && (
                <Alert severity="warning">
                  The encrypted API key cannot be read. Verify the endpoint and every model profile, then enter a
                  replacement key to retain the reviewed settings.
                </Alert>
              )}
              <TextField
                select
                label="API key action"
                value={common.secretAction}
                onChange={(event) => {
                  const secretAction = event.target.value as SecretAction;
                  if (secretAction === 'clear') {
                    setCommon((current) => ({
                      ...current,
                      authType: 'entra_id',
                      secretAction: 'clear',
                      apiKeyValue: '',
                    }));
                  } else setCommonField('secretAction', secretAction);
                }}
              >
                {common.apiKeyConfigured && <MenuItem value="keep">Keep configured key</MenuItem>}
                <MenuItem value="set">{common.apiKeyConfigured ? 'Replace key' : 'Set key'}</MenuItem>
                {common.apiKeyConfigured && <MenuItem value="clear">Clear key and use Entra ID</MenuItem>}
              </TextField>
              {common.secretAction === 'set' && (
                <TextField
                  label="API key"
                  type="password"
                  value={common.apiKeyValue}
                  onChange={(event) => setCommonField('apiKeyValue', event.target.value)}
                  autoComplete="new-password"
                  required
                />
              )}
            </>
          )}

          <TextField
            label="Agent Instructions"
            value={common.agentInstructions}
            onChange={(event) => setCommonField('agentInstructions', event.target.value)}
            multiline
            minRows={4}
          />
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

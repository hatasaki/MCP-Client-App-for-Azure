import React, { useState, useEffect, useMemo } from 'react'; // Import useMemo
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Box,
  Alert,
  Typography,
  Divider,
} from '@mui/material';
import { AzureConfig } from '../types';

interface AzureConfigDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (config: AzureConfig) => void;
  initialConfig?: AzureConfig | null;
  serverConfig?: AzureConfig | null; // Add server config prop
}

const AzureConfigDialog: React.FC<AzureConfigDialogProps> = ({
  open,
  onClose,
  onSave,
  initialConfig,
  serverConfig,
}) => {
  const defaultConfig: AzureConfig = useMemo(
    () => ({
      endpoint: '',
      apiKey: '',
      deployment: '',
      apiVersion: '',
      systemPrompt: '',
      temperature: undefined,
      topP: undefined,
      maxTokens: undefined,
      apiType: 'chat',
      // New Responses API defaults
      reasoningEffort: 'none',
      verbosity: 'none',
      maxCompletionTokens: undefined,
    }),
    []
  ); // Empty dependency array ensures it's created only once

  const [config, setConfig] = useState<AzureConfig>(defaultConfig);
  const [formError, setFormError] = useState<string>('');
  const [apiType, setApiType] = useState<'chat' | 'responses'>(
    (initialConfig?.apiType as any) ?? 'chat'
  );

  useEffect(() => {
    if (open) {
      setFormError('');
      if (initialConfig) {
        const cfg = { ...initialConfig } as any;
        if (cfg.system_prompt) cfg.systemPrompt = cfg.system_prompt;
        if (cfg.top_p !== undefined) cfg.topP = cfg.top_p;
        if (cfg.max_tokens !== undefined) cfg.maxTokens = cfg.max_tokens;
        // Map server-side response params to UI keys
        if (cfg.reasoning_effort !== undefined) cfg.reasoningEffort = cfg.reasoning_effort || 'none';
        if (cfg.max_completion_tokens !== undefined) cfg.maxCompletionTokens = Number(cfg.max_completion_tokens) as any;
        setConfig({ ...defaultConfig, ...cfg });
        setApiType(cfg.apiType ?? cfg.api_type ?? 'chat')
      } else if (serverConfig) {
        const cfg = { ...serverConfig } as any;
        if (cfg.system_prompt) cfg.systemPrompt = cfg.system_prompt;
        if (cfg.top_p !== undefined) cfg.topP = cfg.top_p;
        if (cfg.max_tokens !== undefined) cfg.maxTokens = cfg.max_tokens;
        // Map server-side response params to UI keys
        if (cfg.reasoning_effort !== undefined) cfg.reasoningEffort = cfg.reasoning_effort || 'none';
        if (cfg.max_completion_tokens !== undefined) cfg.maxCompletionTokens = Number(cfg.max_completion_tokens) as any;
        setConfig({ ...defaultConfig, ...cfg });
        setApiType(cfg.apiType ?? cfg.api_type ?? 'chat')
      } else {
        setConfig(defaultConfig);
        setApiType('chat')
      }
    }
  }, [open, initialConfig, serverConfig, defaultConfig]); // Keep defaultConfig in dependency array

  const handleSave = () => {
    if (!config.endpoint.trim() || !config.deployment.trim()) {
      setFormError('Endpoint and deployment name are required.');
      return;
    }
    setFormError('');
    // Prepare config for server
    const prepared = { ...config } as any;
    ['temperature', 'topP', 'maxTokens'].forEach((k) => {
      if (prepared[k] === undefined) {
        // Convert undefined (from untouched optional fields) to empty string
        prepared[k] = '';
      }
    });

    // Map UI keys to server keys for Responses API params
    if (apiType === 'responses') {
      // reasoning_effort: omit when 'none'
      if (prepared.reasoningEffort && prepared.reasoningEffort !== 'none') {
        prepared.reasoning_effort = prepared.reasoningEffort;
      } else {
        prepared.reasoning_effort = '';
      }
      // verbosity: omit when 'none'
      if (prepared.verbosity && prepared.verbosity !== 'none') {
        // use same key name in backend config
        prepared.verbosity = prepared.verbosity;
      } else {
        prepared.verbosity = '';
      }
      // max_completion_tokens: omit when empty/undefined
      if (prepared.maxCompletionTokens !== undefined && prepared.maxCompletionTokens !== null && prepared.maxCompletionTokens !== '') {
        prepared.max_completion_tokens = Number(prepared.maxCompletionTokens);
      } else {
        prepared.max_completion_tokens = '';
      }
    }

    onSave({ ...prepared, apiType });
    onClose();
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Azure OpenAI Settings</DialogTitle>
      <DialogContent sx={{ userSelect: 'text' }}>
        {serverConfig && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" color="text.secondary" gutterBottom>
              Current Settings:
            </Typography>
            <Box sx={{ pl: 2, py: 1, bgcolor: 'grey.100', borderRadius: 1, mb: 2 }}>
              <Typography variant="body2">Endpoint: {serverConfig.endpoint}</Typography>
              <Typography variant="body2">Deployment: {serverConfig.deployment}</Typography>
              <Typography variant="body2">API Version: {serverConfig.apiVersion}</Typography>
              {(serverConfig.system_prompt || serverConfig.systemPrompt) && (
                <Typography variant="body2" sx={{ whiteSpace:'pre-wrap' }}>System Prompt: {serverConfig.system_prompt ?? serverConfig.systemPrompt}</Typography>
              )}
              {(() => {
                const keyPresent = !!((serverConfig as any).apiKey ?? (serverConfig as any).api_key);
                return (
                  <Typography variant="body2">
                    API Key: {keyPresent ? 'set' : 'not set (use EntraID)'}
                  </Typography>
                );
              })()}
            </Box>
            <Divider sx={{ mb: 2 }} />
          </Box>
        )}
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          <TextField
            label="Endpoint"
            value={config.endpoint}
            onChange={(e) => setConfig({ ...config, endpoint: e.target.value })}
            fullWidth
            placeholder="https://your-resource.openai.azure.com"
            error={!!formError && !config.endpoint.trim()} // Updated error condition
            helperText={
              !!formError && !config.endpoint.trim() ? 'Required' : ''
            } // Updated helperText
          />
          <TextField
            label="API Key (blank to use EntraID)"
            type="password"
            value={config.apiKey}
            onChange={(e) => setConfig({ ...config, apiKey: e.target.value })}
            fullWidth
          />
          <TextField
            label="Deployment Name"
            value={config.deployment}
            onChange={(e) => setConfig({ ...config, deployment: e.target.value })}
            fullWidth
            placeholder="gpt-4"
            error={!!formError && !config.deployment.trim()} // Updated error condition
            helperText={
              !!formError && !config.deployment.trim() ? 'Required' : ''
            } // Updated helperText
          />
          <TextField
            label="API Version"
            value={config.apiVersion}
            onChange={(e) => setConfig({ ...config, apiVersion: e.target.value })}
            fullWidth
            placeholder="2023-12-01-preview"
          />
          <TextField
            label="System Prompt (optional)"
            value={config.systemPrompt || ''}
            onChange={(e) => setConfig({ ...config, systemPrompt: e.target.value })}
            fullWidth
            multiline
            rows={3}
            placeholder="(Use default prompt)"
          />
          <TextField
            select
            label="API Type"
            value={apiType}
            SelectProps={{ native: true }}
            onChange={(e) => setApiType(e.target.value as 'chat' | 'responses')}
            fullWidth
          >
            <option value="chat">Chat Completion</option>
            <option value="responses">Responses</option>
          </TextField>
          {/* Hide parameters not supported by Responses API */}
          {apiType === 'chat' && (
            <>
              <Box sx={{ display: 'flex', gap: 2 }}>
                <TextField
                  label="temperature"
                  type="number"
                  InputProps={{ inputProps: { step: 0.1, min: 0, max: 2 } }}
                  value={config.temperature ?? ''}
                  onChange={(e) => setConfig({ ...config, temperature: e.target.value === '' ? undefined : Number(e.target.value) })}
                  fullWidth
                />
                <TextField
                  label="top_p"
                  type="number"
                  InputProps={{ inputProps: { step: 0.05, min: 0, max: 1 } }}
                  value={config.topP ?? ''}
                  onChange={(e) => setConfig({ ...config, topP: e.target.value === '' ? undefined : Number(e.target.value) })}
                  fullWidth
                />
                <TextField
                  label="max_tokens"
                  type="number"
                  InputProps={{ inputProps: { min: 1 } }}
                  value={config.maxTokens ?? ''}
                  onChange={(e) => setConfig({ ...config, maxTokens: e.target.value === '' ? undefined : Number(e.target.value) })}
                  fullWidth
                />
              </Box>
            </>
          )}

          {apiType === 'responses' && (
            <>
              <Box sx={{ display: 'flex', gap: 2 }}>
                <TextField
                  select
                  label="reasoning_effort"
                  value={config.reasoningEffort ?? 'none'}
                  SelectProps={{ native: true }}
                  onChange={(e) => setConfig({ ...config, reasoningEffort: e.target.value as any })}
                  fullWidth
                >
                  <option value="none">none (omit)</option>
                  <option value="minimal">minimal</option>
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                </TextField>
                <TextField
                  select
                  label="verbosity"
                  value={config.verbosity ?? 'none'}
                  SelectProps={{ native: true }}
                  onChange={(e) => setConfig({ ...config, verbosity: e.target.value as any })}
                  fullWidth
                >
                  <option value="none">none (omit)</option>
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                </TextField>
                <TextField
                  label="max_completion_tokens"
                  type="number"
                  InputProps={{ inputProps: { min: 1 } }}
                  value={config.maxCompletionTokens ?? ''}
                  onChange={(e) => setConfig({ ...config, maxCompletionTokens: e.target.value === '' ? undefined : Number(e.target.value) })}
                  fullWidth
                />
              </Box>
            </>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleSave} variant="contained">
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AzureConfigDialog;

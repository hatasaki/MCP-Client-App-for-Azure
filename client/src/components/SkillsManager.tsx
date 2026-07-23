import React, { useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Tooltip,
  Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import UploadFileIcon from '@mui/icons-material/UploadFile';

import { AgentSkill } from '../types';

interface SkillsManagerProps {
  open: boolean;
  onClose: () => void;
  skills: AgentSkill[];
  onChanged: (skills: AgentSkill[]) => void;
  onError: (message: string) => void;
}

const backendUrl = window.location.origin;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

const formatBytes = (value: number) => value >= 1024 * 1024
  ? `${(value / (1024 * 1024)).toFixed(1)} MB`
  : `${Math.max(0, Math.round(value / 1024))} KB`;

const SkillsManager: React.FC<SkillsManagerProps> = ({
  open,
  onClose,
  skills,
  onChanged,
  onError,
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (file.size <= 0 || file.size > MAX_UPLOAD_BYTES) {
      onError('Skill uploads must be larger than 0 bytes and no larger than 10 MB.');
      return;
    }
    const lowerName = file.name.toLowerCase();
    if (!lowerName.endsWith('.md') && !lowerName.endsWith('.zip')) {
      onError('Upload a ZIP bundle or Markdown file containing a valid SKILL.md definition.');
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${backendUrl}/skills/upload`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Skill-Filename': encodeURIComponent(file.name),
        },
        body: await file.arrayBuffer(),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload.detail || 'Skill upload failed.'));
      onChanged(payload.skills || []);
      const count = Array.isArray(payload.uploaded) ? payload.uploaded.length : 0;
      setNotice(`${count} skill${count === 1 ? '' : 's'} installed. Existing names were replaced.`);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : 'Skill upload failed.');
    } finally {
      setBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const remove = async (skill: AgentSkill) => {
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${backendUrl}/skills/${encodeURIComponent(skill.id)}`, {
        method: 'DELETE',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload.detail || 'Skill deletion failed.'));
      onChanged(payload.skills || []);
      setNotice(`${skill.name} was removed.`);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : 'Skill deletion failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>Agent Skills</DialogTitle>
      <DialogContent>
        <Typography color="text.secondary" sx={{ mb: 2 }}>
          Upload Agent Skills as SKILL.md or ZIP. ZIP bundles may contain one or multiple skill directories.
          Skills are instruction and resource only; uploaded scripts are removed and cannot run.
        </Typography>
        <Alert severity="warning" sx={{ mb: 2 }}>
          Skill instructions are supplied to the model. Install only content you trust. Select installed skills separately in each chat.
        </Alert>
        {notice && <Alert severity="success" onClose={() => setNotice(null)} sx={{ mb: 2 }}>{notice}</Alert>}
        <input
          ref={fileInputRef}
          type="file"
          hidden
          accept=".zip,.md,application/zip,text/markdown,text/plain"
          aria-label="Agent Skill upload input"
          onChange={(event) => void upload(event.target.files?.[0])}
        />
        <Button
          variant="contained"
          startIcon={busy ? <CircularProgress size={18} color="inherit" /> : <UploadFileIcon />}
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
          sx={{ mb: 2 }}
        >
          Upload ZIP or SKILL.md
        </Button>
        <List disablePadding>
          {skills.map((skill) => (
            <ListItem
              key={skill.id}
              divider
              secondaryAction={
                <Tooltip title={`Delete ${skill.name}`}>
                  <span>
                    <IconButton
                      edge="end"
                      aria-label={`Delete skill ${skill.name}`}
                      onClick={() => void remove(skill)}
                      disabled={busy}
                    >
                      <DeleteIcon />
                    </IconButton>
                  </span>
                </Tooltip>
              }
            >
              <ListItemText
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                    <Typography fontWeight={600}>{skill.name}</Typography>
                    <Chip size="small" label={`${skill.resourceCount} resources · ${formatBytes(skill.resourceBytes)}`} />
                    {skill.scriptsIgnored && <Chip size="small" color="warning" label="Scripts removed" />}
                  </Box>
                }
                secondary={`${skill.description}\nSource: ${skill.sourceFilename}`}
                secondaryTypographyProps={{ sx: { whiteSpace: 'pre-line' } }}
              />
            </ListItem>
          ))}
          {!skills.length && (
            <ListItem>
              <ListItemText primary="No skills installed" secondary="Upload a SKILL.md file or ZIP bundle." />
            </ListItem>
          )}
        </List>
      </DialogContent>
      <DialogActions><Button onClick={onClose} disabled={busy}>Close</Button></DialogActions>
    </Dialog>
  );
};

export default SkillsManager;

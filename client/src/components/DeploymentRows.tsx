import React from 'react';
import { Box, IconButton, TextField, Typography } from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';

export const deploymentsToRows = (models: string[] = []): string[] => [...models, ''];

export const rowsToDeployments = (rows: string[]): string[] =>
  rows.map((value) => value.trim()).filter(Boolean);

const ensureTrailingBlank = (rows: string[]): string[] => {
  const compact = rows.filter((value, index) => index === rows.length - 1 || value !== '');
  if (!compact.length || compact[compact.length - 1] !== '') compact.push('');
  return compact;
};

interface DeploymentRowsProps {
  rows: string[];
  onChange: (rows: string[]) => void;
}

const DeploymentRows: React.FC<DeploymentRowsProps> = ({ rows, onChange }) => {
  const names = rows.map((value) => value.trim()).filter(Boolean);
  const duplicates = new Set(names.filter((name, index) => names.indexOf(name) !== index));

  const update = (index: number, value: string) => {
    onChange(ensureTrailingBlank(rows.map((item, itemIndex) => itemIndex === index ? value : item)));
  };

  const remove = (index: number) => {
    onChange(ensureTrailingBlank(rows.filter((_, itemIndex) => itemIndex !== index)));
  };

  return (
    <Box>
      {rows.map((value, index) => {
        const name = value.trim();
        const duplicate = !!name && duplicates.has(name);
        const trailingBlank = index === rows.length - 1 && !value;
        return (
          <Box key={index} sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'flex-start' }}>
            <TextField
              label="Model deployment name"
              value={value}
              onChange={(event) => update(index, event.target.value)}
              error={duplicate}
              helperText={duplicate ? 'Duplicate deployment name for this API type.' : ' '}
              size="small"
              fullWidth
            />
            <IconButton
              aria-label={`Delete model deployment row ${index + 1}`}
              onClick={() => remove(index)}
              disabled={trailingBlank}
              size="small"
              sx={{ mt: 0.5 }}
            >
              <DeleteIcon />
            </IconButton>
          </Box>
        );
      })}
      <Typography variant="caption" color="text.secondary">
        Enter a deployment name to add another row. Names must be unique within this API type.
      </Typography>
    </Box>
  );
};

export default DeploymentRows;

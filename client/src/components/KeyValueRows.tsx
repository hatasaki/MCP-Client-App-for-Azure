import React from 'react';
import { Box, IconButton, TextField, Typography } from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';

export interface KeyValueRow {
  key: string;
  value: string;
}

export const recordToRows = (value?: Record<string, string>): KeyValueRow[] => [
  ...Object.entries(value || {}).map(([key, rowValue]) => ({ key, value: rowValue })),
  { key: '', value: '' },
];

export const rowsToRecord = (rows: KeyValueRow[]): Record<string, string> => {
  const result: Record<string, string> = {};
  rows.forEach((row) => {
    const key = row.key.trim();
    if (key) result[key] = row.value;
  });
  return result;
};

const ensureTrailingBlank = (rows: KeyValueRow[]): KeyValueRow[] => {
  const withoutExtraBlanks = rows.filter(
    (row, index) => index === rows.length - 1 || row.key !== '' || row.value !== ''
  );
  const last = withoutExtraBlanks[withoutExtraBlanks.length - 1];
  if (!last || last.key !== '' || last.value !== '') {
    withoutExtraBlanks.push({ key: '', value: '' });
  }
  return withoutExtraBlanks;
};

interface KeyValueRowsProps {
  rows: KeyValueRow[];
  onChange: (rows: KeyValueRow[]) => void;
  keyLabel?: string;
  valueLabel?: string;
  emptyText?: string;
}

const KeyValueRows: React.FC<KeyValueRowsProps> = ({
  rows,
  onChange,
  keyLabel = 'Key',
  valueLabel = 'Value',
  emptyText = 'Enter a key to add another row. Empty values are allowed.',
}) => {
  const keys = rows.map((row) => row.key.trim()).filter(Boolean);
  const duplicateKeys = new Set(keys.filter((key, index) => keys.indexOf(key) !== index));

  const update = (index: number, field: keyof KeyValueRow, value: string) => {
    const next = rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row);
    onChange(ensureTrailingBlank(next));
  };

  const remove = (index: number) => {
    onChange(ensureTrailingBlank(rows.filter((_, rowIndex) => rowIndex !== index)));
  };

  return (
    <Box>
      {rows.map((row, index) => {
        const key = row.key.trim();
        const missingKey = !key && !!row.value;
        const duplicate = !!key && duplicateKeys.has(key);
        const isTrailingBlank = index === rows.length - 1 && !row.key && !row.value;
        return (
          <Box key={index} sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'flex-start' }}>
            <TextField
              label={keyLabel}
              value={row.key}
              onChange={(event) => update(index, 'key', event.target.value)}
              size="small"
              fullWidth
              error={missingKey || duplicate}
              helperText={missingKey ? 'Key is required.' : duplicate ? 'Duplicate key.' : ' '}
            />
            <TextField
              label={valueLabel}
              value={row.value}
              onChange={(event) => update(index, 'value', event.target.value)}
              size="small"
              fullWidth
              helperText=" "
            />
            <IconButton
              aria-label={`Delete ${keyLabel.toLowerCase()} row ${index + 1}`}
              onClick={() => remove(index)}
              disabled={isTrailingBlank}
              size="small"
              sx={{ mt: 0.5 }}
            >
              <DeleteIcon />
            </IconButton>
          </Box>
        );
      })}
      <Typography variant="caption" color="text.secondary">{emptyText}</Typography>
    </Box>
  );
};

export default KeyValueRows;

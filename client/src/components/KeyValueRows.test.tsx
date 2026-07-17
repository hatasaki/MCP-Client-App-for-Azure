import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import KeyValueRows, { KeyValueRow, recordToRows } from './KeyValueRows';

const Harness = () => {
  const [rows, setRows] = useState<KeyValueRow[]>(recordToRows());
  return <KeyValueRows rows={rows} onChange={setRows} keyLabel="Header" valueLabel="Value" />;
};

test('automatically appends a trailing editable row without an Add button', () => {
  render(<Harness />);

  expect(screen.getAllByLabelText('Header')).toHaveLength(1);
  fireEvent.change(screen.getAllByLabelText('Header')[0], { target: { value: 'Authorization' } });
  expect(screen.getAllByLabelText('Header')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: /^add$/i })).not.toBeInTheDocument();
});

test('flags duplicate keys inline', () => {
  render(<Harness />);

  fireEvent.change(screen.getAllByLabelText('Header')[0], { target: { value: 'X-Test' } });
  fireEvent.change(screen.getAllByLabelText('Header')[1], { target: { value: 'X-Test' } });

  expect(screen.getAllByText('Duplicate key.')).toHaveLength(2);
});

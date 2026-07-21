import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import DeploymentRows, { deploymentsToRows } from './DeploymentRows';

const Harness = () => {
  const [rows, setRows] = useState(deploymentsToRows());
  return <DeploymentRows rows={rows} onChange={setRows} />;
};

test('automatically appends a model deployment row', () => {
  render(<Harness />);

  fireEvent.change(screen.getAllByLabelText('Model deployment name')[0], { target: { value: 'gpt-a' } });
  expect(screen.getAllByLabelText('Model deployment name')).toHaveLength(2);
});

test('flags duplicate deployment names', () => {
  render(<Harness />);

  fireEvent.change(screen.getAllByLabelText('Model deployment name')[0], { target: { value: 'gpt-a' } });
  fireEvent.change(screen.getAllByLabelText('Model deployment name')[1], { target: { value: 'gpt-a' } });
  expect(screen.getAllByText('Duplicate deployment name for this API type.')).toHaveLength(2);
});

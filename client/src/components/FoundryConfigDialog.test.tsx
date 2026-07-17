import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import FoundryConfigDialog from './FoundryConfigDialog';

const renderDialog = () => render(
  <FoundryConfigDialog open onClose={() => undefined} onSave={() => undefined} />
);

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

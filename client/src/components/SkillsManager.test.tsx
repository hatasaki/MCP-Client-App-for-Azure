import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import SkillsManager from './SkillsManager';
import { AgentSkill } from '../types';

const installed: AgentSkill = {
  id: 'writing-guide',
  name: 'writing-guide',
  description: 'Use for writing tasks.',
  contentHash: 'a'.repeat(64),
  resourceCount: 1,
  resourceBytes: 1024,
  scriptsIgnored: true,
  sourceFilename: 'bundle.zip',
};

beforeEach(() => {
  jest.restoreAllMocks();
});

test('uploads a SKILL.md file and reports installed skills', async () => {
  const onChanged = jest.fn();
  const onError = jest.fn();
  jest.spyOn(global, 'fetch').mockResolvedValue({
    ok: true,
    json: async () => ({ uploaded: [installed], skills: [installed] }),
  } as Response);
  const file = new File(['skill'], 'SKILL.md', { type: 'text/markdown' });
  Object.defineProperty(file, 'arrayBuffer', {
    value: async () => new Uint8Array([115, 107, 105, 108, 108]).buffer,
  });

  render(
    <SkillsManager
      open
      onClose={jest.fn()}
      skills={[]}
      onChanged={onChanged}
      onError={onError}
    />
  );

  fireEvent.change(screen.getByLabelText('Agent Skill upload input'), { target: { files: [file] } });

  await waitFor(() => expect(onChanged).toHaveBeenCalledWith([installed]));
  expect(onError).not.toHaveBeenCalled();
  expect(global.fetch).toHaveBeenCalledWith('http://localhost/skills/upload', expect.objectContaining({
    method: 'POST',
    headers: expect.objectContaining({ 'X-Skill-Filename': 'SKILL.md' }),
  }));
  expect(await screen.findByText(/1 skill installed/)).toBeInTheDocument();
});

test('shows script removal and deletes a skill', async () => {
  const onChanged = jest.fn();
  jest.spyOn(global, 'fetch').mockResolvedValue({
    ok: true,
    json: async () => ({ skills: [] }),
  } as Response);

  render(
    <SkillsManager
      open
      onClose={jest.fn()}
      skills={[installed]}
      onChanged={onChanged}
      onError={jest.fn()}
    />
  );

  expect(screen.getByText('Scripts removed')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Delete skill writing-guide' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalledWith([]));
  expect(global.fetch).toHaveBeenCalledWith('http://localhost/skills/writing-guide', { method: 'DELETE' });
});

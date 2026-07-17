import fs from 'fs';
import path from 'path';

test('does not enable raw HTML rendering for untrusted chat content', () => {
  const source = fs.readFileSync(path.join(__dirname, 'MarkdownRenderer.tsx'), 'utf8');
  const packageJson = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', '..', 'package.json'), 'utf8')
  );

  expect(source).not.toMatch(/rehypeRaw|rehype-raw/);
  expect(packageJson.dependencies['rehype-raw']).toBeUndefined();
});
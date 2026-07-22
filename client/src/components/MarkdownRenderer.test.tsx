import fs from 'fs';
import path from 'path';

test('enables raw HTML only through sanitization and routes Mermaid to strict renderer', () => {
  const markdownSource = fs.readFileSync(path.join(__dirname, 'MarkdownRenderer.tsx'), 'utf8');
  const mermaidSource = fs.readFileSync(path.join(__dirname, 'MermaidDiagram.tsx'), 'utf8');
  const packageJson = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', '..', 'package.json'), 'utf8')
  );

  expect(markdownSource).toMatch(/rehypeRaw/);
  expect(markdownSource).toMatch(/rehypeSanitize/);
  expect(markdownSource).toMatch(/safeHtmlSchema/);
  expect(markdownSource).toMatch(/'style'/);
  expect(markdownSource).toMatch(/'form'/);
  expect(markdownSource).toMatch(/href: \['http', 'https', 'mailto'\]/);
  expect(markdownSource).toMatch(/language\.toLowerCase\(\) === 'mermaid'/);
  expect(mermaidSource).toMatch(/securityLevel: 'strict'/);
  expect(mermaidSource).toMatch(/DOMPurify\.sanitize/);
  expect(packageJson.dependencies['rehype-raw']).toBeDefined();
  expect(packageJson.dependencies['rehype-sanitize']).toBeDefined();
  expect(packageJson.dependencies.mermaid).toBeDefined();
});
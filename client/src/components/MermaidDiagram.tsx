import React, { useEffect, useId, useState } from 'react';
import { Alert, Box, CircularProgress } from '@mui/material';
import DOMPurify from 'dompurify';

interface MermaidDiagramProps {
  chart: string;
}

let initialized = false;

const MermaidDiagram: React.FC<MermaidDiagramProps> = ({ chart }) => {
  const reactId = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setError(null);

    const render = async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        if (!initialized) {
          mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            htmlLabels: false,
            suppressErrorRendering: true,
            theme: 'neutral',
          });
          initialized = true;
        }
        const rendered = await mermaid.render(`mermaid-${reactId}`, chart.trim());
        if (!cancelled) {
          setSvg(DOMPurify.sanitize(rendered.svg, {
            USE_PROFILES: { svg: true, svgFilters: true },
            FORBID_TAGS: ['script', 'foreignObject'],
          }));
        }
      } catch {
        if (!cancelled) setError('This Mermaid diagram could not be rendered.');
      }
    };

    void render();
    return () => {
      cancelled = true;
    };
  }, [chart, reactId]);

  if (error) return <Alert severity="warning">{error}</Alert>;
  if (!svg) return <CircularProgress aria-label="Rendering Mermaid diagram" size={20} />;

  return (
    <Box
      role="img"
      aria-label="Mermaid diagram"
      sx={{
        my: 1.5,
        maxWidth: '100%',
        overflow: 'auto',
        '& svg': { display: 'block', maxWidth: '100%', height: 'auto', margin: '0 auto' },
      }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
};

export default MermaidDiagram;

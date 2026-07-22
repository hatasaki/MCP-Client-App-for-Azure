import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { Box, Typography, Paper } from '@mui/material';

import MermaidDiagram from './MermaidDiagram';

interface MarkdownRendererProps {
  content: string;
  color?: string;
}

const safeHtmlSchema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames || []).filter((tag) => ![
    'button',
    'form',
    'input',
    'math',
    'option',
    'select',
    'style',
    'svg',
    'textarea',
  ].includes(tag)),
  attributes: {
    ...defaultSchema.attributes,
    '*': (defaultSchema.attributes?.['*'] || []).filter((attribute) => attribute !== 'style'),
    code: [
      ...(defaultSchema.attributes?.code || []),
      ['className', /^language-[A-Za-z0-9_-]+$/],
    ],
  },
  protocols: {
    ...defaultSchema.protocols,
    href: ['http', 'https', 'mailto'],
    src: ['http', 'https'],
  },
};

const safeUrl = (value: string) => /^(https?:|mailto:)/i.test(value.trim()) ? value : '';

const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content, color = 'inherit' }) => {  return (
    <Box sx={{ 
      color,
      maxWidth: '100%',
      overflow: 'hidden',
      wordWrap: 'break-word',
      overflowWrap: 'break-word',
    }}><ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, safeHtmlSchema]]}
        urlTransform={safeUrl}
        components={{
          // 見出し
          h1: ({ children }) => (
            <Typography 
              variant="h4" 
              component="h1" 
              gutterBottom 
              sx={{ 
                color,
                borderBottom: 1,
                borderColor: 'divider',
                paddingBottom: 1,
                marginTop: 2,
                marginBottom: 2
              }}
            >
              {children}
            </Typography>
          ),
          h2: ({ children }) => (
            <Typography 
              variant="h5" 
              component="h2" 
              gutterBottom 
              sx={{ 
                color,
                borderBottom: 1,
                borderColor: 'divider',
                paddingBottom: 0.5,
                marginTop: 1.5,
                marginBottom: 1.5
              }}
            >
              {children}
            </Typography>
          ),
          h3: ({ children }) => (
            <Typography 
              variant="h6" 
              component="h3" 
              gutterBottom 
              sx={{ 
                color,
                marginTop: 1.5,
                marginBottom: 1
              }}
            >
              {children}
            </Typography>
          ),
          h4: ({ children }) => (
            <Typography variant="subtitle1" component="h4" gutterBottom sx={{ color, fontWeight: 'bold' }}>
              {children}
            </Typography>
          ),
          h5: ({ children }) => (
            <Typography variant="subtitle2" component="h5" gutterBottom sx={{ color, fontWeight: 'bold' }}>
              {children}
            </Typography>
          ),
          h6: ({ children }) => (
            <Typography variant="body1" component="h6" gutterBottom sx={{ color, fontWeight: 'bold' }}>
              {children}
            </Typography>
          ),
          
          // 段落
          p: ({ children }) => (
            <Typography 
              variant="body1" 
              paragraph 
              sx={{ 
                color, 
                margin: '0.5em 0',
                lineHeight: 1.6,
                '&:last-child': {
                  marginBottom: 0
                }
              }}
            >
              {children}
            </Typography>
          ),
          
          // リスト
          ul: ({ children }) => (
            <Box 
              component="ul" 
              sx={{ 
                pl: 3, 
                color,
                my: 1,
                '& ul': {
                  marginTop: 0,
                  marginBottom: 0
                }
              }}
            >
              {children}
            </Box>
          ),
          ol: ({ children }) => (
            <Box 
              component="ol" 
              sx={{ 
                pl: 3, 
                color,
                my: 1,
                '& ol': {
                  marginTop: 0,
                  marginBottom: 0
                }
              }}
            >
              {children}
            </Box>
          ),
          li: ({ children }) => (
            <Typography 
              component="li" 
              variant="body1" 
              sx={{ 
                color, 
                mb: 0.5,
                lineHeight: 1.6
              }}
            >
              {children}
            </Typography>          ),
          
          // コードブロック
          code: ({ className, children, ...props }) => {
            const isInline = !className;
            const language = className ? className.replace('language-', '') : '';
            const codeText = String(children).replace(/\n$/, '');

            if (!isInline && language.toLowerCase() === 'mermaid') {
              return <MermaidDiagram chart={codeText} />;
            }
            
            return !isInline ? (
              <Box
                sx={{
                  maxWidth: '100%',
                  overflow: 'hidden',
                  minWidth: 0,
                  width: '100%',
                }}
              >
                <Paper
                elevation={1}
                sx={{
                  p: 2,
                  my: 1,
                  bgcolor: color === 'white' ? 'rgba(255,255,255,0.1)' : 'grey.100',
                  overflow: 'auto',
                  fontSize: '0.875rem',
                  fontFamily: 'monospace',
                  position: 'relative',
                  maxWidth: '100%',
                  width: '100%',
                  minWidth: 0,
                  boxSizing: 'border-box',
                  '& pre': {
                    margin: 0,
                    padding: 0,
                    background: 'transparent',
                    whiteSpace: 'pre',
                    overflow: 'auto',
                    maxWidth: '100%',
                    minWidth: 0,
                    width: 0, // 重要: 幅を強制的に0にして親コンテナに依存
                  },
                  '& code': {
                    background: 'transparent',
                    padding: 0,
                    whiteSpace: 'pre',
                    display: 'block',
                    maxWidth: '100%',
                    minWidth: 0,
                    width: 0, // 重要: 幅を強制的に0にして親コンテナに依存
                  }
                }}
              >
                {language && (
                  <Typography
                    variant="caption"
                    sx={{
                      position: 'absolute',
                      top: 8,
                      right: 12,
                      color: 'text.secondary',
                      fontSize: '0.75rem',
                    }}
                  >
                    {language}
                  </Typography>
                )}                <code className={className} {...props}>
                  {codeText}
                </code>
              </Paper>
              </Box>
            ) : (
              <Box
                component="code"
                sx={{
                  px: 0.5,
                  py: 0.25,
                  bgcolor: color === 'white' ? 'rgba(255,255,255,0.2)' : 'grey.200',
                  borderRadius: 1,
                  fontSize: '0.875rem',
                  fontFamily: 'monospace',
                  color: color === 'white' ? 'rgba(255,255,255,0.9)' : 'text.primary',
                }}
                {...props}
              >
                {children}
              </Box>
            );
          },
          
          // 引用
          blockquote: ({ children }) => (
            <Paper
              sx={{
                borderLeft: 4,
                borderColor: color === 'white' ? 'rgba(255,255,255,0.5)' : 'primary.main',
                pl: 2,
                py: 1,
                my: 1,
                bgcolor: color === 'white' ? 'rgba(255,255,255,0.1)' : 'grey.50',
                fontStyle: 'italic',
              }}
            >
              {children}
            </Paper>
          ),
          
          // テーブル
          table: ({ children }) => (
            <Paper sx={{ overflow: 'auto', my: 1 }}>
              <Box component="table" sx={{ width: '100%', borderCollapse: 'collapse' }}>
                {children}
              </Box>
            </Paper>
          ),
          th: ({ children }) => (
            <Box
              component="th"
              sx={{
                p: 1,
                border: 1,
                borderColor: 'divider',
                bgcolor: 'grey.100',
                fontWeight: 'bold',
                textAlign: 'left',
              }}
            >
              {children}
            </Box>
          ),
          td: ({ children }) => (
            <Box
              component="td"
              sx={{
                p: 1,
                border: 1,
                borderColor: 'divider',
              }}
            >
              {children}
            </Box>
          ),
          
          // リンク
          a: ({ children, href }) => (
            <Box
              component="a"
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              sx={{
                color: color === 'white' ? 'lightblue' : 'primary.main',
                textDecoration: 'underline',
                '&:hover': {
                  textDecoration: 'none',
                },
              }}
            >
              {children}
            </Box>
          ),
          
          // 強調
          strong: ({ children }) => (
            <Box component="strong" sx={{ fontWeight: 'bold', color }}>
              {children}
            </Box>
          ),
          em: ({ children }) => (
            <Box component="em" sx={{ fontStyle: 'italic', color }}>
              {children}
            </Box>
          ),
          
          // 水平線
          hr: () => (
            <Box
              component="hr"
              sx={{
                border: 'none',
                borderTop: 1,
                borderColor: color === 'white' ? 'rgba(255,255,255,0.3)' : 'divider',
                my: 2,
              }}
            />
          ),
          
        }}
      >
        {content}
      </ReactMarkdown>
    </Box>
  );
};

export default MarkdownRenderer;

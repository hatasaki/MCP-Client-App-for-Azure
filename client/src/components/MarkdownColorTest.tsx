import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import MarkdownRenderer from './MarkdownRenderer';

const MarkdownColorTest: React.FC = () => {
  const testMarkdown = `
# カラー対応テスト

通常のテキストです。

<span style="color:#d93f2b;">赤色のタイトル</span>

<span style="color:#2b5cd9;">青色のテキスト</span>

<div style="color:#2bd95c; background-color:#f0f0f0; padding:10px; border-radius:5px;">
緑色のテキストと背景色付きのボックス
</div>

## その他のHTMLタグテスト

<b>太字テキスト</b>

<i>斜体テキスト</i>

<u>下線付きテキスト</u>

<s>取り消し線付きテキスト</s>

<mark style="background-color:#ffff00;">ハイライトテキスト</mark>

<small style="color:#666;">小さいテキスト</small>

## 複合テスト

<span style="color:#ff6b35; font-weight:bold; font-size:1.2em;">大きな太字のオレンジテキスト</span>

<div style="border:2px solid #3498db; padding:15px; margin:10px 0; border-radius:8px;">
<span style="color:#e74c3c; font-weight:bold;">ボーダー付きボックス内の赤いテキスト</span>
</div>
  `;

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" gutterBottom>
        Markdownカラー機能テスト
      </Typography>
      
      <Paper sx={{ p: 2, mb: 2, bgcolor: 'grey.100' }}>
        <Typography variant="h6" gutterBottom>
          アシスタントメッセージ（通常背景）
        </Typography>
        <MarkdownRenderer content={testMarkdown} color="inherit" />
      </Paper>
      
      <Paper sx={{ p: 2, bgcolor: 'primary.main', color: 'white' }}>
        <Typography variant="h6" gutterBottom sx={{ color: 'white' }}>
          ユーザーメッセージ（ダーク背景）
        </Typography>
        <MarkdownRenderer content={testMarkdown} color="white" />
      </Paper>
    </Box>
  );
};

export default MarkdownColorTest;

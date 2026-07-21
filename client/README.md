# Microsoft Foundry React Frontend

This folder contains the Material UI frontend for *MCP Client for Microsoft Foundry*. It uses server-canonical schema v4 Foundry settings, API-specific model profiles with one chat default model, a persistent per-chat model selector, Socket.IO token streaming, batched MCP approval, and real run cancellation.

## Scripts

| Command | Purpose |
|---------|---------|
| `npm start` | Start dev server on <http://localhost:3000> |
| `npm run build` | Create production build in `build/` |
| `npm test -- --watchAll=false` | Run unit tests once with Jest |

Use `npm ci --legacy-peer-deps` for a clean install. The project was bootstrapped with Create React App and uses TypeScript. API keys are never persisted in browser storage; the frontend receives only `apiKeyConfigured` and explicit keep/set/clear actions.

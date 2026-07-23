# Microsoft Foundry React Frontend

This folder contains the Material UI frontend for *MCP Client for Microsoft Foundry*. It uses server-canonical schema v4 Foundry settings and schema v5 chat sessions, API-specific model profiles with one chat default model, persistent per-chat model and Agent Skills selectors, Socket.IO token streaming, batched MCP approval, file attachments, sanitized Markdown/HTML/Mermaid rendering, and real run cancellation.

The top-level **Skills** dialog uploads and manages `SKILL.md` files or ZIP bundles through the backend REST API. Installed skills are selected independently in each chat. Uploaded scripts are never executed.

## Scripts

| Command | Purpose |
|---------|---------|
| `npm start` | Start dev server on <http://localhost:3000> |
| `npm run build` | Create production build in `build/` |
| `npm test -- --watchAll=false` | Run unit tests once with Jest |

Use `npm ci --legacy-peer-deps` for a clean install. The project was bootstrapped with Create React App and uses TypeScript. API keys are never persisted in browser storage; the frontend receives only `apiKeyConfigured` and explicit keep/set/clear actions.

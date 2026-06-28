# ODS Dashboard

Real-time system status dashboard for ODS.

## Features

- **GPU Monitoring**: VRAM usage, utilization %, temperature with warning colors
- **Service Health**: Status of all Docker services with health indicators
- **Model Management**: Download, switch, and monitor LLM models
- **Voice Status**: Whisper STT, Kokoro TTS, and ODS Talk readiness
- **Workflow Management**: n8n workflow status and controls
- **Settings**: System configuration and service controls

## Tech Stack

- **Frontend**: React + Vite + Tailwind CSS
- **Backend API**: FastAPI (`dashboard-api/`)
- **Deployment**: Nginx serving the built React app, proxying API calls to the backend

## Development

```bash
cd ods/dashboard

# Install dependencies
npm install

# Development server (hot reload)
npm run dev

# Production build
npm run build
```

The dev server runs on `http://localhost:5173` and proxies API calls to the dashboard-api on port 3002.

## Production

The dashboard is built and served via Docker. See the `Dockerfile` and `nginx.conf` for the production setup. The nginx layer handles:

- Serving the built React app
- Proxying `/api/*` requests to the dashboard-api
- Injecting the `Authorization` header for API authentication

## Structure

```
dashboard/
├── src/
│   ├── components/     # Sidebar, SetupWizard, PreFlightChecks, etc.
│   ├── pages/          # Dashboard, Models, ODS Talk, Extensions, Settings
│   ├── hooks/          # useSystemStatus, useModels, useVersion, etc.
│   ├── App.jsx         # Router and layout
│   └── main.jsx        # Entry point
├── public/             # Static assets
├── Dockerfile          # Multi-stage build (npm build + nginx)
├── nginx.conf          # Production proxy config
├── vite.config.js      # Build configuration
└── tailwind.config.js  # Tailwind theme
```

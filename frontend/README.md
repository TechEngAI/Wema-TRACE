# TRACE Frontend Application

React & TypeScript Single-Page Application (SPA) for the TRACE Transaction Investigation Platform.

## Architectural Boundaries
- **Presentation Only**: Creates and lists cases, uploads synthetic evidence, and visualizes backend correlation, deterministic analysis, explanations, and evidence packs. Human review remains unsupported because the backend returns `501`.
- **Zero Client Analysis**: The frontend never computes analysis outcomes, calculates confidence scores, or determines fraud/discrepancy states. All findings are received from the backend deterministic rules engine.
- **Zero Secrets**: Contains NO database credentials, private API keys, or administrative tokens. Only communicates with the main TRACE backend via `VITE_API_BASE_URL`.

## Technology Stack
- **Framework**: React 19 with TypeScript
- **Bundler & Dev Server**: Vite
- **Styles**: Modern Vanilla CSS tokens with dark-mode aesthetic (no bulky CSS frameworks)
- **Contracts**: Typed shared contracts located in `src/types/contracts.ts`

## Directory Structure
```text
frontend/
├── src/
│   ├── assets/               # Static assets and icons
│   ├── services/
│   │   └── api.ts            # Typed HTTP client & health probe
│   ├── types/
│   │   └── contracts.ts      # Frozen domain contracts (outcomes, strengths, schemas)
│   ├── App.tsx               # Foundation status & baseline health dashboard
│   ├── index.css             # Design system tokens and baseline styling
│   ├── main.tsx              # React DOM mounting entrypoint
│   └── vite-env.d.ts         # Vite client type references
├── .env.example              # Safe placeholder environment configuration
├── index.html                # Application HTML root
├── package.json              # Dependency manifest & scripts
├── tsconfig.json             # Root TypeScript project references
├── tsconfig.app.json         # Application TypeScript compiler options
├── tsconfig.node.json        # Node / Vite configuration TypeScript options
├── vite.config.ts            # Vite bundler configuration
└── README.md
```

## Local Development Workflow

### 1. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Set `VITE_API_BASE_URL=http://localhost:8000` for the default local backend. The older
`VITE_API_URL` is accepted only as a compatibility fallback; new configuration should use
`VITE_API_BASE_URL`.

### 2. Dependency Installation
```bash
npm install
```

### 3. Start Development Server
```bash
npm run dev
```
By default, the Vite development server starts at [http://localhost:5173](http://localhost:5173).

### 4. Build for Production / Type Check
```bash
npm run build
```
Generates production bundle in `dist/`.

## Security boundary

Use synthetic evidence only. TRACE currently has no authentication or authorization on its
case and evidence endpoints, so this frontend must not be publicly deployed for real financial
data. It never calls the standalone `ai_module/` service; outcome and strength always come from
the main backend's deterministic rules engine.

Human reviews are separate records. The reviewer name is explicitly unverified metadata in this
MVP, and accepting or disputing a finding never changes the machine outcome or strength.

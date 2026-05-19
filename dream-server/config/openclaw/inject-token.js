// Inject gateway auth token into Control UI so it auto-connects
// Runs at container startup before the gateway starts
//
// Three tasks:
//   1. Patch the runtime config (origins, flags, auth, model names)
//   2. Inject auto-token.js into the Control UI HTML (CSP-compliant)
//   3. Fix model references to match what llama-server actually serves
//
// IMPORTANT: The gateway sets Content-Security-Policy: script-src 'self'
// which blocks inline scripts. So we must create an EXTERNAL .js file
// and reference it via <script src="./auto-token.js"> to satisfy CSP.

const fs = require('fs');
const path = require('path');

const token = process.env.OPENCLAW_GATEWAY_TOKEN || '';
const EXTERNAL_PORT = process.env.OPENCLAW_EXTERNAL_PORT || '7860';
const LLM_MODEL = process.env.LLM_MODEL || '';
const GGUF_FILE = process.env.GGUF_FILE || '';
const OPENCLAW_LLM_URL = process.env.OPENCLAW_LLM_URL || '';
const BIND_ADDRESS = (process.env.BIND_ADDRESS || '127.0.0.1').trim();
const AUTO_TOKEN_ALLOWED = ['127.0.0.1', 'localhost', '::1'].includes(BIND_ADDRESS);

// On AMD/Lemonade, compose.amd.yaml sets OLLAMA_URL to
// "http://llama-server:8080/api" (Lemonade's Ollama-compat endpoint).
// Models there are exposed as "extra.<GGUF_FILE>".  When going through
// LiteLLM, LLM_MODEL is fine because the wildcard route rewrites it.
// Detect Lemonade by the trailing "/api" path (NVIDIA's llama.cpp URL
// never has it — its default is "http://llama-server:8080" or via LiteLLM).
const OLLAMA_URL = process.env.OLLAMA_URL || '';
const _isLemonade = /\/api\/?$/.test(OLLAMA_URL);
const EFFECTIVE_MODEL = (_isLemonade && GGUF_FILE) ? `extra.${GGUF_FILE}` : LLM_MODEL;
const CONFIG_PATH = path.join(process.env.HOME || '/home/node', '.openclaw', 'openclaw.json');
const HTML_PATH = process.env.OPENCLAW_CONTROL_UI_HTML || '/app/dist/control-ui/index.html';
const JS_PATH = process.env.OPENCLAW_AUTO_TOKEN_JS || '/app/dist/control-ui/auto-token.js';

// ── Device auth policy (#1270) ────────────────────────────────────────────────
//
// Device auth (pairing) is a SEPARATE gate from token auth. Token auth + a
// valid injected gateway token still auto-connect on loopback with device auth
// ON, so device auth defaults to ON regardless of bind mode. Disabling it is an
// explicit, deliberate opt-in via OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH.
//
// Behaviour:
//   - unset / anything but "true"  -> device auth ON (delete the disable key so
//                                     the gateway default of device auth ON
//                                     takes effect)
//   - exactly "true"               -> device auth DISABLED + loud startup banner
//
// allowInsecureAuth is a distinct transport concern and is intentionally NOT
// touched here.
const DISABLE_DEVICE_AUTH =
  (process.env.OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH || '').trim().toLowerCase() === 'true';
let _deviceAuthWarned = false;

// Apply the device-auth decision to a controlUi object in place. When disabled,
// emit the warning banner exactly once per process (Part 1 and Part 3 share
// this helper). Logging is redaction-safe: no token or secret is ever printed.
function applyDeviceAuthPolicy(controlUi) {
  if (DISABLE_DEVICE_AUTH) {
    controlUi.dangerouslyDisableDeviceAuth = true;
    if (!_deviceAuthWarned) {
      _deviceAuthWarned = true;
      console.warn('[inject-token] ┌──────────────────────────────────────────────────────────┐');
      console.warn('[inject-token] │  *** SECURITY WARNING: DEVICE AUTH DISABLED ***          │');
      console.warn('[inject-token] │                                                          │');
      console.warn('[inject-token] │  OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH=true is set.   │');
      console.warn('[inject-token] │  The OpenClaw gateway will NOT require device pairing.   │');
      console.warn('[inject-token] │  Anyone who can reach the gateway (LAN/Tailscale/0.0.0.0 │');
      console.warn('[inject-token] │  binds) gets an UNAUTHENTICATED agent with exec / read / │');
      console.warn('[inject-token] │  write tools. Unset this variable to restore the secure  │');
      console.warn('[inject-token] │  default (device auth ON).                               │');
      console.warn('[inject-token] └──────────────────────────────────────────────────────────┘');
    }
  } else {
    // Remove the key entirely so the gateway falls back to its secure default
    // (device auth ON). Deleting is more robust than setting false against
    // pre-PR configs persisted on the named Docker volume.
    delete controlUi.dangerouslyDisableDeviceAuth;
  }
}

// ── Part 1: Patch runtime config ──────────────────────────────────────────────

try {
  let config = {};
  if (fs.existsSync(CONFIG_PATH)) {
    config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  }

  if (!config.gateway) config.gateway = {};
  if (!config.gateway.controlUi) config.gateway.controlUi = {};

  // Add external port origins so the Control UI can connect through Docker port mapping
  const origins = config.gateway.controlUi.allowedOrigins || [];
  const needed = [
    `http://localhost:${EXTERNAL_PORT}`,
    `http://127.0.0.1:${EXTERNAL_PORT}`,
  ];
  try {
    const hostname = require('os').hostname();
    if (hostname) needed.push(`http://${hostname}:${EXTERNAL_PORT}`);
  } catch {}
  // When BIND_ADDRESS=0.0.0.0, the installer writes the host's LAN IP into
  // HOST_LAN_IP so the Control UI can be reached from other devices on the
  // network. os.hostname() returns the container ID inside Docker, not the
  // host LAN address, so this env-passthrough is the only reliable source.
  const hostLanIp = process.env.HOST_LAN_IP;
  if (hostLanIp) {
    needed.push(`http://${hostLanIp}:${EXTERNAL_PORT}`);
    needed.push(`https://${hostLanIp}:${EXTERNAL_PORT}`);
  }
  for (const origin of needed) {
    if (!origins.includes(origin)) origins.push(origin);
  }
  config.gateway.controlUi.allowedOrigins = origins;

  // Ensure controlUi flags are set for local use
  config.gateway.controlUi.allowInsecureAuth = true;
  // Device auth defaults ON; disable only via explicit opt-in env (#1270).
  applyDeviceAuthPolicy(config.gateway.controlUi);
  delete config.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback;  // defang upgrades from pre-PR installs that wrote this flag to the persistent volume

  // Keep token auth (required for LAN bind) with token from env
  if (token) {
    config.gateway.auth = { mode: 'token', token: token };
  }

  // Fix model references to match what llama-server actually serves
  if (EFFECTIVE_MODEL) {
    // Find the provider name (first key under models.providers)
    const providerName = config.models?.providers
      ? Object.keys(config.models.providers)[0]
      : null;

    if (providerName && config.models.providers[providerName]) {
      const provider = config.models.providers[providerName];

      // Route through LiteLLM when OLLAMA_URL points to it, and pass credentials
      const ollamaUrl = process.env.OLLAMA_URL || '';
      const litellmKey = process.env.LITELLM_KEY || '';
      if (ollamaUrl) {
        const newBase = ollamaUrl.replace(/\/$/, '') + '/v1';
        if (provider.baseUrl !== newBase) {
          console.log(`[inject-token] updated provider baseUrl: ${provider.baseUrl} -> ${newBase}`);
          provider.baseUrl = newBase;
        }
        if (litellmKey && provider.apiKey !== litellmKey) {
          provider.apiKey = litellmKey;
          console.log(`[inject-token] updated provider apiKey from env`);
        }
      }

      // Update model list — replace the first model's id and name
      if (Array.isArray(provider.models) && provider.models.length > 0) {
        const oldId = provider.models[0].id;
        if (oldId !== EFFECTIVE_MODEL) {
          provider.models[0].id = EFFECTIVE_MODEL;
          provider.models[0].name = EFFECTIVE_MODEL;
          console.log(`[inject-token] updated provider model: ${oldId} -> ${EFFECTIVE_MODEL}`);
        }
      }
    }

    // Update agents.defaults model references
    if (config.agents?.defaults) {
      const d = config.agents.defaults;
      const fullOld = d.model?.primary || '';
      if (fullOld && providerName) {
        const fullNew = `${providerName}/${EFFECTIVE_MODEL}`;
        if (fullOld !== fullNew) {
          d.model = { primary: fullNew };
          // Rebuild models map
          d.models = { [fullNew]: {} };
          // Fix subagent model
          if (d.subagents) d.subagents.model = fullNew;
          console.log(`[inject-token] updated agent model refs: ${fullOld} -> ${fullNew}`);
        }
      }
    }
  }

  // Override LLM baseUrl for Token Spy monitoring (if OPENCLAW_LLM_URL is set)
  const providers = config.models?.providers || config.providers || {};
  if (OPENCLAW_LLM_URL && Object.keys(providers).length > 0) {
    for (const [name, provider] of Object.entries(providers)) {
      if (provider.baseUrl) {
        const oldUrl = provider.baseUrl;
        provider.baseUrl = OPENCLAW_LLM_URL;
        console.log(`[inject-token] monitoring: provider ${name} baseUrl: ${oldUrl} -> ${OPENCLAW_LLM_URL}`);
      }
    }
  }

  // Enable OpenAI-compatible HTTP API (opt-in via OPENCLAW_HTTP_API=true)
  if (process.env.OPENCLAW_HTTP_API === 'true') {
    if (!config.gateway.http) config.gateway.http = {};
    if (!config.gateway.http.endpoints) config.gateway.http.endpoints = {};
    config.gateway.http.endpoints.chatCompletions = { enabled: true };
    console.log('[inject-token] enabled HTTP /v1/chat/completions endpoint');
  }

  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf8');
  console.log('[inject-token] patched runtime config:', CONFIG_PATH);

  // Confirm token was injected without leaking it to stdout.
  // Anyone with `docker logs dream-openclaw` access could otherwise harvest
  // the gateway token from a URL printed here.
  if (token) {
    console.log(`[inject-token] ┌─────────────────────────────────────────────┐`);
    console.log(`[inject-token] │ OpenClaw Control UI ready on port ${EXTERNAL_PORT}.`);
    console.log(`[inject-token] │ Token redacted; paste from .env to sign in. │`);
    console.log(`[inject-token] └─────────────────────────────────────────────┘`);
  }
} catch (err) {
  console.error('[inject-token] config patch warning:', err.message);
}

// ── Part 2: Inject token into Control UI ──────────────────────────────────────

if (token && fs.existsSync(HTML_PATH)) {
  try {
    // 1. Create external JS file for token bootstrap.
    //
    // Do not write the gateway token to the unauthenticated static asset when
    // Dream Server is LAN-bound. With BIND_ADDRESS=0.0.0.0, anyone on the LAN
    // could fetch http://<host>:<port>/auto-token.js and harvest the token.
    //
    // For the default localhost-only bind, the asset is only reachable from the
    // same machine, so restore auto-connect UX by placing the token into the
    // Control UI settings store.
    let jsCode;
    if (AUTO_TOKEN_ALLOWED) {
      jsCode = [
        '(function() {',
        '  var k = "openclaw.control.settings.v1";',
        '  var s = {};',
        '  try { s = JSON.parse(localStorage.getItem(k) || "{}"); } catch(e) {}',
        '  s.token = ' + JSON.stringify(token) + ';',
        '  s.gatewayUrl = (location.protocol === "https:" ? "wss://" : "ws://") + location.host;',
        '  localStorage.setItem(k, JSON.stringify(s));',
        '})();',
      ].join('\n');
    } else {
      jsCode = [
        '// Auto-token injection disabled to prevent gateway-token disclosure via',
        '// this unauthenticated static asset when Dream Server is LAN-bound.',
        '// Paste the token manually from .env (OPENCLAW_TOKEN) into the Control UI.',
        '(function(){ /* no-op */ })();',
      ].join('\n');
    }
    fs.writeFileSync(JS_PATH, jsCode);

    // 2. Inject <script src> tag as first element in <head> (satisfies CSP 'self')
    let html = fs.readFileSync(HTML_PATH, 'utf8');
    // Remove any previous injection (inline or external)
    html = html.replace(/<script[^>]*auto-token[^>]*>[^<]*<\/script>/g, '');
    html = html.replace(/<script[^>]*src="\.\/auto-token\.js"[^>]*><\/script>/g, '');
    // Add external script reference at start of <head>
    html = html.replace('<head>', '<head><script src="./auto-token.js"></script>');
    fs.writeFileSync(HTML_PATH, html);

    if (AUTO_TOKEN_ALLOWED) {
      console.log('[inject-token] wrote localhost-only auto-token.js');
    } else {
      console.log('[inject-token] wrote placeholder auto-token.js (LAN token disclosure mitigated; manual sign-in required)');
    }
  } catch (err) {
    console.error('[inject-token] UI injection warning:', err.message);
  }
} else {
  if (!token) console.warn('[inject-token] no OPENCLAW_GATEWAY_TOKEN set, skipping UI injection');
  if (!fs.existsSync(HTML_PATH)) console.warn('[inject-token] Control UI HTML not found at', HTML_PATH);
}

// ── Part 3: Create merged config ─────────────────────────────────────────────

try {
  const primaryConfigPath = process.env.OPENCLAW_CONFIG || '/config/openclaw.json';
  if (fs.existsSync(primaryConfigPath)) {
    const primary = JSON.parse(fs.readFileSync(primaryConfigPath, 'utf8'));

    // Enable HTTP API in merged config (opt-in via OPENCLAW_HTTP_API=true)
    if (process.env.OPENCLAW_HTTP_API === 'true') {
      if (!primary.gateway) primary.gateway = {};
      if (!primary.gateway.http) primary.gateway.http = {};
      if (!primary.gateway.http.endpoints) primary.gateway.http.endpoints = {};
      primary.gateway.http.endpoints.chatCompletions = { enabled: true };
    }

    // Ensure gateway.controlUi settings are present (required when --bind lan
    // exposes the gateway on a non-loopback interface).  Part 1 patches these
    // into ~/.openclaw/openclaw.json but that write may fail (EACCES on
    // Docker volume), so we must also set them here in the merged config.
    if (!primary.gateway) primary.gateway = {};
    // gateway.mode is required by OpenClaw v2026.3.8+; without it the
    // gateway refuses to start.
    if (!primary.gateway.mode) primary.gateway.mode = 'local';
    if (!primary.gateway.controlUi) primary.gateway.controlUi = {};
    primary.gateway.controlUi.allowInsecureAuth = true;
    // Device auth defaults ON; disable only via explicit opt-in env (#1270).
    applyDeviceAuthPolicy(primary.gateway.controlUi);
    delete primary.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback;  // defang carry-over (mirrors Part 1)
    const extPort = process.env.OPENCLAW_EXTERNAL_PORT || '7860';
    const origins = primary.gateway.controlUi.allowedOrigins || [];
    const mergedNeeded = [`http://localhost:${extPort}`, `http://127.0.0.1:${extPort}`];
    // Mirror Part 1: append the host LAN IP when BIND_ADDRESS=0.0.0.0 so the
    // Control UI accepts requests from LAN clients hitting the host directly.
    const mergedHostLanIp = process.env.HOST_LAN_IP;
    if (mergedHostLanIp) {
      mergedNeeded.push(`http://${mergedHostLanIp}:${extPort}`);
      mergedNeeded.push(`https://${mergedHostLanIp}:${extPort}`);
    }
    for (const o of mergedNeeded) {
      if (!origins.includes(o)) origins.push(o);
    }
    primary.gateway.controlUi.allowedOrigins = origins;

    // Fix provider baseUrl and model IDs to match the actual LLM endpoint
    const ollamaUrl = process.env.OLLAMA_URL || '';
    if (ollamaUrl) {
      const provs = primary.models?.providers || primary.providers || {};
      for (const [name, prov] of Object.entries(provs)) {
        if (prov.baseUrl) {
          const oldUrl = prov.baseUrl;
          prov.baseUrl = ollamaUrl.replace(/\/$/, '') + '/v1';
          if (oldUrl !== prov.baseUrl) {
            console.log(`[inject-token] merged config: provider ${name} baseUrl: ${oldUrl} -> ${prov.baseUrl}`);
          }
        }
        // Patch model IDs to match what the backend actually serves
        if (EFFECTIVE_MODEL && Array.isArray(prov.models) && prov.models.length > 0) {
          const oldId = prov.models[0].id;
          if (oldId !== EFFECTIVE_MODEL) {
            prov.models[0].id = EFFECTIVE_MODEL;
            prov.models[0].name = EFFECTIVE_MODEL;
            console.log(`[inject-token] merged config: provider ${name} model: ${oldId} -> ${EFFECTIVE_MODEL}`);
          }
        }
      }
    }

    // Patch agent model references in merged config
    if (EFFECTIVE_MODEL && primary.agents?.defaults) {
      const provs = primary.models?.providers || {};
      const providerName = Object.keys(provs)[0];
      if (providerName) {
        const d = primary.agents.defaults;
        const fullNew = `${providerName}/${EFFECTIVE_MODEL}`;
        const fullOld = d.model?.primary || '';
        if (fullOld && fullOld !== fullNew) {
          d.model = { primary: fullNew };
          d.models = { [fullNew]: {} };
          if (d.subagents) d.subagents.model = fullNew;
          console.log(`[inject-token] merged config: agent model: ${fullOld} -> ${fullNew}`);
        }
      }
    }

    const mergedPath = '/tmp/openclaw-config.json';
    fs.writeFileSync(mergedPath, JSON.stringify(primary, null, 2), 'utf8');
    console.log('[inject-token] created merged config at', mergedPath);
  }
} catch (err) {
  console.error('[inject-token] merged config warning:', err.message);
}

// ── Part 4: OpenAI-compat shim (opt-in via OPENCLAW_HTTP_API=true) ──────────
// OpenClaw serves /v1/chat/completions but not /v1/models.
// Open WebUI needs /v1/models to discover available models.
// This shim runs on port 18790, serves /v1/models, and proxies everything
// else to the gateway on 18789.

if (process.env.OPENCLAW_HTTP_API === 'true') {
  try {
    const shimScript = `
const http = require('http');
const GATEWAY_PORT = 18789;
const MODELS = JSON.stringify({
  object: 'list',
  data: [{ id: 'openclaw', object: 'model', created: ${Math.floor(Date.now() / 1000)}, owned_by: 'openclaw-gateway' }],
});

let restarts = 0;
function startServer() {
  const server = http.createServer((req, res) => {
    if (req.url === '/v1/models') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(MODELS);
    }
    const proxy = http.request({ hostname: '127.0.0.1', port: GATEWAY_PORT, path: req.url, method: req.method, headers: req.headers }, (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
    });
    proxy.on('error', () => { res.writeHead(502); res.end('gateway unavailable'); });
    req.pipe(proxy);
  });
  server.on('error', (err) => {
    console.error('[openai-shim] server error: ' + err.message);
    if (restarts < 5) {
      restarts++;
      const delay = restarts * 2000;
      console.error('[openai-shim] restarting in ' + delay + 'ms (attempt ' + restarts + '/5)');
      setTimeout(startServer, delay);
    } else {
      console.error('[openai-shim] too many failures, giving up');
    }
  });
  server.listen(18790, '0.0.0.0', () => {
    restarts = 0;
    console.log('[openai-shim] /v1/models + proxy on :18790');
  });
}
startServer();

process.on('uncaughtException', (err) => {
  console.error('[openai-shim] uncaught exception: ' + err.message);
});
process.on('SIGTERM', () => {
  console.error('[openai-shim] received SIGTERM, shutting down');
  process.exit(0);
});
`;
    fs.writeFileSync('/tmp/openai-shim.js', shimScript);

    const { spawn } = require('child_process');
    const child = spawn('node', ['/tmp/openai-shim.js'], {
      detached: true,
      stdio: 'inherit',
    });
    child.unref();
    console.log('[inject-token] started openai-shim (pid %d)', child.pid);
  } catch (err) {
    console.error('[inject-token] shim warning:', err.message);
  }
}

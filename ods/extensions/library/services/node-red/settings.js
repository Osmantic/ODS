'use strict';

const password = process.env.NODE_RED_ADMIN_PASSWORD || '';
const secret = process.env.NODE_RED_CREDENTIAL_SECRET || '';
const bytes = Buffer.byteLength(password, 'utf8');
if (bytes < 16 || bytes > 72) {
    throw new Error('NODE_RED_ADMIN_PASSWORD must contain 16 to 72 UTF-8 bytes');
}
if (!/^[a-f0-9]{64}$/i.test(secret)) {
    throw new Error('NODE_RED_CREDENTIAL_SECRET must contain 64 hexadecimal characters');
}
const hash = require('bcryptjs').hashSync(password, 12);
// Do not expose bootstrap credentials to function-node process environments.
delete process.env.NODE_RED_ADMIN_PASSWORD;
delete process.env.NODE_RED_CREDENTIAL_SECRET;

module.exports = {
    uiPort: 1880,
    uiHost: '0.0.0.0',
    userDir: '/data',
    flowFile: 'flows.json',
    credentialSecret: secret,
    adminAuth: {
        type: 'credentials',
        users: [{ username: 'ods', password: hash, permissions: '*' }],
        sessionExpiryTime: 43200,
    },
    httpNodeRoot: '/api',
    httpNodeAuth: { user: 'ods', pass: hash },
    contextStorage: {
        default: { module: 'localfilesystem' },
        memory: { module: 'memory' },
    },
    functionExternalModules: false,
    functionTimeout: 30,
    editorTheme: { projects: { enabled: false } },
    logging: { console: { level: 'info', metrics: false, audit: false } },
};

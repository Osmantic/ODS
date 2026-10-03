'use strict';
const fs = require('node:fs');
const { createRequire } = require('node:module');
const password = process.env.VERDACCIO_PASSWORD || '';
if (!/^[a-fA-F0-9]{64}$/.test(password)) {
  throw new Error('VERDACCIO_PASSWORD must be 64 hexadecimal characters');
}
// Resolve the bcrypt implementation shipped with this pinned registry image.
const registryRequire = createRequire('/usr/local/lib/node_modules/verdaccio/package.json');
const authRequire = createRequire(registryRequire.resolve('verdaccio-htpasswd'));
const bcrypt = authRequire('bcryptjs');
fs.mkdirSync('/tmp/ods-verdaccio', { recursive: true, mode: 0o700 });
fs.writeFileSync('/tmp/ods-verdaccio/htpasswd', `ods:${bcrypt.hashSync(password, 12)}\n`, { mode: 0o600 });

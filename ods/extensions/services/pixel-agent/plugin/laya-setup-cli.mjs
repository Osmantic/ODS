import {realpathSync} from 'node:fs';
import {configureLayaPortal} from './laya-setup.mjs';

// An extension setup hook supplies INSTALL_DIR and the resolved local port.
const [root, port = '8017', composeFile, ...extra] = process.argv.slice(2);
if (!root || extra.length || !/^[0-9]+$/.test(port)) {
  throw new Error('Usage: node laya-setup-cli.mjs INSTALL_DIR [PORT] [COMPOSE_FILE]');
}
const result = configureLayaPortal({installRoot: realpathSync(root), port: Number(port), composeFile});
console.log(JSON.stringify({state: result.state, port: result.port}));

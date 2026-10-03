#!/bin/sh
set -eu
export PATH="$PWD/node_modules/.bin:$PATH"
node scripts/check-db.js
node ods-initialize.mjs
node scripts/update-tracker.js
exec node server.js

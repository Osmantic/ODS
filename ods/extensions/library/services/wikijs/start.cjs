'use strict';
if (!/^[a-fA-F0-9]{64}$/.test(process.env.DB_PASS || '')) {
  console.error('WIKIJS_DB_PASSWORD must contain exactly 64 hexadecimal characters');
  process.exit(1);
}

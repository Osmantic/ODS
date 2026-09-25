export function environment(input) {
  const password = input.UMAMI_INITIAL_PASSWORD || '';
  if (Buffer.byteLength(password) < 12 || Buffer.byteLength(password) > 72) throw Error('Initial password must contain 12 to 72 UTF-8 bytes');
  if (!/^[a-fA-F0-9]{64}$/.test(input.UMAMI_TWO_FACTOR_KEY || '')) throw Error('Two-factor key must contain 64 hexadecimal characters');
  if ((input.UMAMI_APP_SECRET || '').length < 32) throw Error('App secret must contain at least 32 characters');
  if (!input.UMAMI_DATABASE_PASSWORD) throw Error('Database password is required');
  const url = new URL('postgresql://umami@umami-db:5432/umami');
  url.password = encodeURIComponent(input.UMAMI_DATABASE_PASSWORD);
  return {...input, DATABASE_URL:url.href, APP_SECRET:input.UMAMI_APP_SECRET, TWO_FACTOR_ENCRYPTION_KEY:input.UMAMI_TWO_FACTOR_KEY};
}

for (const key of ['REACTIVE_RESUME_DB_PASSWORD', 'AUTH_SECRET']) {
  if (!/^[a-f0-9]{64}$/i.test(process.env[key] || '')) {
    throw new Error(`${key} must contain 64 hexadecimal characters`);
  }
}
process.env.DATABASE_URL = `postgresql://resume:${process.env.REACTIVE_RESUME_DB_PASSWORD}@reactive-resume-db:5432/resume`;
delete process.env.REACTIVE_RESUME_DB_PASSWORD;

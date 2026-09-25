import {PrismaPg} from '@prisma/adapter-pg';
import {PrismaClient} from './generated/prisma/client.js';
import bcrypt from 'bcryptjs';

const prisma = new PrismaClient({adapter:new PrismaPg({connectionString:process.env.DATABASE_URL})});
try {
  const initialHash = '$2b$10$BUli0c.muyCW1ErNJc3jL.vFRFtFJWrT8/GcR4A.sUdCznaXiqFXa';
  const id = '41e2b680-648e-4b09-bcd7-3e2b10c06264';
  const rows = await prisma.$queryRaw`SELECT user_id FROM "user" WHERE user_id = ${id}::uuid AND password = ${initialHash}`;
  if(rows.length) {
    const hash = await bcrypt.hash(process.env.UMAMI_INITIAL_PASSWORD,10);
    // Conditional update also protects a password changed between read and write.
    await prisma.$executeRaw`UPDATE "user" SET password = ${hash} WHERE user_id = ${id}::uuid AND password = ${initialHash}`;
  }
} catch {
  console.error('Umami account initialization failed; HTTP server was not started');
  process.exitCode = 1;
} finally { await prisma.$disconnect(); }

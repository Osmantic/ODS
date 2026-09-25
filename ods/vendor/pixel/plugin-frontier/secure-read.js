import { constants } from "node:fs";
import { open } from "node:fs/promises";

const READ_FLAGS = constants.O_RDONLY
  | (constants.O_NOFOLLOW ?? 0)
  | (constants.O_CLOEXEC ?? 0)
  | (constants.O_NONBLOCK ?? 0);

export async function readBoundedText(path, maximum) {
  const handle = await open(path, READ_FLAGS);
  try {
    const details = await handle.stat();
    if (!details.isFile() || details.size < 0 || details.size > maximum) {
      throw new Error("broker record is missing, invalid, or too large");
    }
    const chunks = [];
    let total = 0;
    while (total <= maximum) {
      const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, maximum + 1 - total));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      chunks.push(buffer.subarray(0, bytesRead));
      total += bytesRead;
    }
    if (total > maximum) throw new Error("broker record is missing, invalid, or too large");
    return Buffer.concat(chunks, total).toString("utf8");
  } finally {
    await handle.close();
  }
}

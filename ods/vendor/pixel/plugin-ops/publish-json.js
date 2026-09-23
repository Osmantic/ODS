import { randomBytes } from "node:crypto";
import { link, open, unlink } from "node:fs/promises";
import { join } from "node:path";


const SAFE_ID = /^[a-z][a-z0-9-]{1,127}$/;


export async function publishBrokerJson(directory, id, value) {
  if (!SAFE_ID.test(id)) throw new Error("unsafe broker record ID");
  const destination = join(directory, `${id}.json`);
  const temporary = join(directory, `.${id}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`);
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.chmod(0o640);
    await handle.close();
    handle = undefined;
    await link(temporary, destination);
    return destination;
  } finally {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}

// The model supplies no Docker arguments. This fixed transport uses the same
// bounded preview protocol as the Linux UDS broker; it grants no new tools.
import { execFile } from "node:child_process";

const DOCKER = "/Applications/Docker.app/Contents/Resources/bin/docker";
const ARGS = Object.freeze(["exec", "-i", "ods-pixel-workspace-preview",
  "python3", "/source/workspace_preview.py", "request"]);

export function dockerWorkspacePreviewRequest(payload, { signal } = {}) {
  if (process.platform !== "darwin") return Promise.reject(new Error("native preview requires macOS"));
  if (signal?.aborted) return Promise.reject(new Error("preview cancelled"));
  const docker = process.env.PIXEL_PREVIEW_DOCKER ?? DOCKER;
  if (!docker.startsWith('/') || /[\0\r\n]/.test(docker)
      || docker.split('/').slice(1).some(part => !part || part === '.' || part === '..')) {
    return Promise.reject(new Error("invalid native preview Docker executable"));
  }
  const body = JSON.stringify(payload);
  if (Buffer.byteLength(body) > 2048) return Promise.reject(new Error("preview request too large"));
  return new Promise((resolve, reject) => {
    const child = execFile(docker, ARGS, {
      signal, timeout: 30000, maxBuffer: 8192, encoding: "utf8",
      killSignal: "SIGKILL",
    }, (error, stdout) => {
      if (error) return reject(new Error("native preview transport unavailable"));
      try {
        if (!stdout.endsWith("\n") || stdout.slice(0, -1).includes("\n")) throw new Error();
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error("invalid native preview response"));
      }
    });
    child.stdin.on("error", () => {}); // execFile reports an exited broker.
    child.stdin.end(body);
  });
}

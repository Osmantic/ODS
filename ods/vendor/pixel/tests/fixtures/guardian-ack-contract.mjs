// Owner-private acknowledgement handshake contract shared by the
// guardian-refresh-fail watcher fixture and its test. The ack path is a
// singular owner-private file that the test writes only after it has observed
// and validated the ready lease, so the injected refresh failure is ordered
// deterministically instead of by timing luck. Every rule is also unit-tested
// in the guardian-lease suite.
import { isAbsolute, relative, resolve, sep } from "node:path";

export function assertOwnerPrivateAckPath(ackPath, root) {
  if (typeof ackPath !== "string" || ackPath.length === 0 || ackPath.includes("\0") || /[\r\n\s]/u.test(ackPath)) throw new Error("fixture ack path is unsafe or noncanonical");
  if (typeof root !== "string" || root.length === 0 || root.includes("\0") || /[\r\n\s]/u.test(root)) throw new Error("fixture ack root is unsafe or noncanonical");
  const resolvedAck = resolve(ackPath);
  const resolvedRoot = resolve(root);
  if (!isAbsolute(ackPath) || resolvedAck !== ackPath || !isAbsolute(root) || resolvedRoot !== root) throw new Error("fixture ack path or root is noncanonical");
  const rel = relative(resolvedRoot, resolvedAck);
  if (rel === "" || rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel)) throw new Error("fixture ack path is not beneath the owner-private root");
  return ackPath;
}

export function renderAckEnvironment(name, value) {
  if (!/^[A-Z0-9_]+$/u.test(name)) throw new Error("fixture ack environment name is invalid");
  if (typeof value !== "string" || value.length === 0 || value.includes("\0") || /[\r\n]/u.test(value)) throw new Error("fixture ack environment value is unsafe");
  if (/\s/u.test(value)) throw new Error("fixture ack environment value contains whitespace");
  return `Environment=${name}=${value}`;
}

import { basename, dirname, join, relative } from "node:path/posix";

const protectedHomePrefixes = ["/home/", "/root/", "/run/user/"];

export function protectedHomeRuntimeMountRoot(home, executablePath) {
  if (!protectedHomePrefixes.some((prefix) => executablePath.startsWith(prefix))) return null;

  const relativeToHome = relative(home, executablePath);
  if (relativeToHome && relativeToHome !== ".." && !relativeToHome.startsWith("../")) {
    return join(home, relativeToHome.split("/")[0]);
  }

  const parent = dirname(executablePath);
  return basename(parent) === "bin" ? dirname(parent) : parent;
}

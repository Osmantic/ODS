import { useMemo, useState } from "react";
import Button from "../components/Button";

interface Props {
  message: string;
  onRetry: () => void;
}

export default function ErrorPage({ message, onRetry }: Props) {
  const [copyState, setCopyState] = useState<"idle" | "pending" | "copied" | "failed">("idle");
  const diagnostics = useMemo(() => [
    `Error: ${message}`,
    `Platform: ${navigator.platform}`,
    `Time: ${new Date().toISOString()}`,
    `UserAgent: ${navigator.userAgent}`,
  ].join("\n"), [message]);

  const copyDiagnostics = async () => {
    setCopyState("pending");
    if (!navigator.clipboard?.writeText) {
      setCopyState("failed");
      return;
    }
    try {
      await navigator.clipboard.writeText(diagnostics);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-6">
        <span className="text-3xl text-red-400">!</span>
      </div>

      <h2 className="text-2xl font-bold mb-3">Something Went Wrong</h2>

      <div className="bg-gray-900 border border-red-900/30 rounded-lg p-4 mb-6 w-full max-w-md">
        <p className="text-sm text-red-300 font-mono whitespace-pre-wrap text-left">
          {message}
        </p>
      </div>

      <div className="space-y-2 mb-8 text-sm text-gray-500 max-w-md text-left">
        <p>Things to try:</p>
        <ul className="list-disc list-inside space-y-1">
          <li>Make sure Docker Desktop is running</li>
          <li>Check that you have a stable internet connection</li>
          <li>Try running the installer again</li>
          <li>
            If the problem persists, copy the diagnostics below and open an
            issue on GitHub
          </li>
        </ul>
      </div>

      <div className="flex gap-3">
        <Button variant="ghost" onClick={copyDiagnostics} disabled={copyState === "pending"}>
          {copyState === "pending" ? "Copying..." : "Copy Diagnostics"}
        </Button>
        <Button onClick={onRetry}>Try Again</Button>
      </div>
      {copyState === "copied" && (
        <p role="status" className="mt-4 text-sm text-green-400">Diagnostics copied.</p>
      )}
      {copyState === "failed" && (
        <div className="mt-4 w-full max-w-md">
          <p role="alert" className="mb-2 text-sm text-yellow-400">
            Automatic copy failed. Select and copy the diagnostics below.
          </p>
          <textarea
            aria-label="Diagnostics to copy manually"
            readOnly
            rows={6}
            value={diagnostics}
            onFocus={(event) => event.currentTarget.select()}
            className="w-full rounded-lg bg-gray-900 p-3 text-xs font-mono text-gray-300"
          />
        </div>
      )}
    </div>
  );
}

import Button from "../components/Button";

interface Props {
  message: string;
  onRetry: () => void;
}

export default function ErrorPage({ message, onRetry }: Props) {
  const copyDiagnostics = () => {
    const info = [
      `Error: ${message}`,
      `Platform: ${navigator.platform}`,
      `Time: ${new Date().toISOString()}`,
      `UserAgent: ${navigator.userAgent}`,
    ].join("\n");
    navigator.clipboard.writeText(info);
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-full py-8 px-8 text-center">
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
        <Button variant="ghost" onClick={copyDiagnostics}>
          Copy Diagnostics
        </Button>
        <Button onClick={onRetry}>Try Again</Button>
      </div>
    </div>
  );
}

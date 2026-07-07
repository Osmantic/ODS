import Button from "../components/Button";
import { openODSserver } from "../hooks/useTauri";

export default function Complete() {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className="text-6xl mb-6">&#10024;</div>
      <h2 className="text-3xl font-bold mb-3">You're All Set</h2>
      <p className="text-gray-400 mb-8 max-w-md">
        ODS is running on your machine. Your AI is completely local,
        private, and yours.
      </p>

      <div className="bg-gray-900 rounded-xl p-6 mb-8 w-full max-w-md text-left space-y-3">
        <div className="flex justify-between">
          <span className="text-sm text-gray-500">Chat UI</span>
          <span className="text-sm text-ods-400 font-mono">
            localhost:3000
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-sm text-gray-500">Dashboard</span>
          <span className="text-sm text-ods-400 font-mono">
            localhost:3001
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-sm text-gray-500">API</span>
          <span className="text-sm text-ods-400 font-mono">
            localhost:8080/v1
          </span>
        </div>
      </div>

      <div className="flex gap-3">
        <Button variant="secondary" onClick={() => window.close()}>
          Close Installer
        </Button>
        <Button onClick={() => openODSserver()}>Open ODS</Button>
      </div>

      <p className="mt-8 text-xs text-gray-600 max-w-sm">
        To manage ODS later, use the Dashboard at localhost:3001 or run
        "ods" from your terminal.
      </p>
    </div>
  );
}

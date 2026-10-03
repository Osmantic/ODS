import { useEffect, useState } from "react";
import Button from "../components/Button";
import { getPortalUrl, openODSserver } from "../hooks/useTauri";

export default function Complete() {
  const [portalUrl, setPortalUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    getPortalUrl()
      .then((url) => {
        if (active) setPortalUrl(url);
      })
      .catch((err) => {
        if (active) setError(String(err));
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className="text-6xl mb-6">&#10024;</div>
      <h2 className="text-3xl font-bold mb-3">You're All Set</h2>
      <p className="text-gray-400 mb-8 max-w-md">
        ODS is running on your machine. Your AI is completely local,
        private, and yours.
      </p>

      <div className="bg-gray-900 rounded-xl p-6 mb-8 w-full max-w-md text-left">
        <p className="text-sm text-gray-500 mb-2">Portal</p>
        {portalUrl ? (
          <p className="text-sm text-ods-400 font-mono break-all">{portalUrl}</p>
        ) : (
          <p className="text-sm text-gray-400">
            Portal address unavailable. Check the installer output for the installed Portal URL.
          </p>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-400 mb-4">{error}</p>
      )}

      <div className="flex gap-3">
        <Button variant="secondary" onClick={() => window.close()}>
          Close Installer
        </Button>
        <Button
          disabled={!portalUrl}
          onClick={() => {
            setError(null);
            openODSserver().catch((err) => setError(String(err)));
          }}
        >
          Open ODS
        </Button>
      </div>

      <p className="mt-8 text-xs text-gray-600 max-w-sm">
        To manage ODS later, use Portal or run
        "ods" from your terminal.
      </p>
    </div>
  );
}

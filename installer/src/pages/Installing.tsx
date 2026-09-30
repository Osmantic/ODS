import { useEffect, useRef, useState } from "react";
import { startInstall, cancelInstall, getInstallProgress, type ProgressInfo } from "../hooks/useTauri";

interface Props {
  tier: number;
  features: string[];
  installDir?: string;
  onComplete: () => void;
  onError: (msg: string) => void;
}

const PHASE_LABELS: Record<string, string> = {
  preflight: "Running preflight checks",
  detection: "Detecting hardware",
  docker: "Setting up Docker",
  images: "Downloading container images",
  services: "Starting services",
  health: "Checking service health",
  complete: "Finishing up",
};

export default function Installing({
  tier,
  features,
  installDir,
  onComplete,
  onError,
}: Props) {
  const [progress, setProgress] = useState<ProgressInfo>({
    phase: "starting",
    percent: 0,
    message: "Starting installation...",
    error: null,
  });
  const started = useRef(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    // Start the install
    startInstall(tier, features, installDir).then(() => {
      onComplete();
    }).catch((e) => {
      onError(String(e));
    });

    // Poll for progress
    const interval = setInterval(async () => {
      try {
        const p = await getInstallProgress();
        setProgress(p);
        if (p.error) {
          clearInterval(interval);
          onError(p.error);
        }
        if (p.percent >= 100) {
          clearInterval(interval);
        }
      } catch {
        // Ignore polling errors
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [tier, features, installDir, onComplete, onError]);

  const phaseLabel =
    PHASE_LABELS[progress.phase] || progress.message || "Working...";

  return (
    <div className="flex flex-col items-center justify-center h-full px-8">
      <h2 className="text-2xl font-bold mb-2">Installing ODS</h2>
      <p className="text-gray-400 mb-10 text-center max-w-md">
        This will take a few minutes. Container images and AI models are being
        downloaded.
      </p>

      {/* Progress bar */}
      <div className="w-full max-w-md mb-4">
        <div className="h-3 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-ods-600 to-ods-400 rounded-full transition-all duration-700"
            style={{ width: `${progress.percent}%` }}
          />
        </div>
      </div>

      <div className="flex justify-between w-full max-w-md mb-8">
        <span className="text-sm text-gray-400">{phaseLabel}</span>
        <span className="text-sm text-gray-500">{progress.percent}%</span>
      </div>

      {/* Phase dots */}
      <div className="flex gap-2">
        {Object.keys(PHASE_LABELS).map((phase) => {
          const currentIdx = Object.keys(PHASE_LABELS).indexOf(progress.phase);
          const thisIdx = Object.keys(PHASE_LABELS).indexOf(phase);
          const done = thisIdx < currentIdx;
          const active = phase === progress.phase;
          return (
            <div
              key={phase}
              className={`w-2 h-2 rounded-full transition-colors ${
                done
                  ? "bg-ods-500"
                  : active
                    ? "bg-ods-400 animate-pulse"
                    : "bg-gray-700"
              }`}
              title={PHASE_LABELS[phase]}
            />
          );
        })}
      </div>

      <button
        type="button"
        disabled={cancelling}
        onClick={async () => {
          setCancelling(true);
          setCancelError(null);
          // The rejected start_install promise surfaces "Installation
          // cancelled." through onError once the child is terminated.
          try {
            await cancelInstall();
          } catch (error: unknown) {
            // Keep monitoring the running installation if stopping it fails.
            setCancelError(String(error));
          } finally {
            setCancelling(false);
          }
        }}
        className="mt-8 px-4 py-2 text-sm rounded-md border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 transition-colors disabled:opacity-50"
      >
        {cancelling ? "Cancelling..." : "Cancel installation"}
      </button>

      {cancelError && (
        <p role="alert" className="mt-3 text-sm text-red-400 text-center max-w-sm">
          {cancelError}
        </p>
      )}

      <p className="mt-6 text-xs text-gray-600 text-center max-w-sm">
        Cancelling or closing this window stops the active download or installer process.
        Background services may continue. Interrupted downloads may need cleanup before retrying.
      </p>
    </div>
  );
}

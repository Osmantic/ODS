import { useEffect, useRef, useState } from "react";
import { startInstall, getInstallProgress, type ProgressInfo } from "../hooks/useTauri";

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
  const [progressUnavailable, setProgressUnavailable] = useState(false);
  const installation = useRef<Promise<string> | null>(null);

  useEffect(() => {
    let active = true;
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    // The command owns the installation. Each effect subscribes to the same
    // result, including StrictMode's setup/cleanup/setup cycle.
    if (!installation.current) {
      installation.current = startInstall(tier, features, installDir);
    }
    installation.current.then(
      () => {
        settled = true;
        clearTimeout(timer);
        if (active) onComplete();
      },
      (error: unknown) => {
        settled = true;
        clearTimeout(timer);
        if (active) onError(String(error));
      },
    );

    const poll = async () => {
      try {
        const next = await getInstallProgress();
        if (active && !settled) {
          setProgress(next);
          setProgressUnavailable(false);
        }
      } catch {
        if (active && !settled) {
          setProgressUnavailable(true);
        }
      }
      // Schedule after the response: a slow IPC call must not accumulate
      // concurrent polls or publish an older response over a newer one.
      if (active && !settled) timer = setTimeout(poll, 2000);
    };
    timer = setTimeout(poll, 2000);

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [tier, features, installDir, onComplete, onError]);

  const phaseLabel =
    progressUnavailable
      ? "Progress unavailable; installation is still running"
      : PHASE_LABELS[progress.phase] || progress.message || "Working...";

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

      <p className="mt-10 text-xs text-gray-600 text-center max-w-sm">
        Please don't close this window. If the install is interrupted, you can
        re-run the installer and it will resume where it left off.
      </p>
    </div>
  );
}

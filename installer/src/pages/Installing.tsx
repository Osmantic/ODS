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
  const started = useRef(false);

  // App recreates onComplete and onError on every render, so an effect that
  // lists them re-runs whenever the parent renders. Reach them through a ref
  // instead, and keep the effects below independent of the render cycle.
  const callbacks = useRef({ onComplete, onError });
  useEffect(() => {
    callbacks.current = { onComplete, onError };
  });

  // Starting the install is not idempotent, so it is guarded — including
  // against StrictMode's development double-invoke, which mounts twice.
  useEffect(() => {
    if (started.current) return;
    started.current = true;

    startInstall(tier, features, installDir)
      .then(() => callbacks.current.onComplete())
      .catch((e) => callbacks.current.onError(String(e)));
    // Install parameters are fixed for the life of this screen; re-running with
    // new ones would start a second install.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Polling is separate and unguarded, so a teardown is always followed by a
  // fresh interval. Folding it into the effect above meant the started guard
  // short-circuited the re-run, leaving the cleared interval with no
  // replacement and the progress bar frozen at 0% for the whole install.
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const p = await getInstallProgress();
        setProgress(p);
        if (p.error) {
          clearInterval(interval);
          callbacks.current.onError(p.error);
        } else if (p.percent >= 100) {
          clearInterval(interval);
        }
      } catch {
        // A dropped poll is not fatal; the next tick picks the state back up.
      }
    }, 2000);

    return () => clearInterval(interval);
  }, []);

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

      <p className="mt-10 text-xs text-gray-600 text-center max-w-sm">
        Please don't close this window. If the install is interrupted, you can
        re-run the installer and it will resume where it left off.
      </p>
    </div>
  );
}

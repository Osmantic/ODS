import { useEffect, useRef, useState } from "react";
import { startInstall, getInstallProgress, type ProgressInfo } from "../hooks/useTauri";

interface Props {
  tier: number;
  features: string[];
  installDir?: string;
  onComplete: () => void;
  onError: (msg: string) => void;
}

// The phase ids ods_progress emits, in the order install-core.sh sources the
// phases (ods/installers/phases/01-preflight .. 13-summary). "setup" is the
// Tauri side's own clone-and-configure step before the script starts, and
// "complete" is the success write it makes afterwards.
//
// The old list was keyed on ids the installer never emits — it was missing
// features, requirements, directories, devtools, offline, amd-tuning and
// summary, and carried a "complete" that only the backend sends. That was
// moot while get_install_progress reported the wizard phase here, but it
// would have left half the ladder dark once it stopped.
const PHASES: { id: string; label: string }[] = [
  { id: "setup", label: "Preparing" },
  { id: "preflight", label: "Running preflight checks" },
  { id: "detection", label: "Detecting hardware" },
  { id: "features", label: "Selecting features" },
  { id: "requirements", label: "Checking system requirements" },
  { id: "docker", label: "Setting up Docker" },
  { id: "directories", label: "Preparing installation directory" },
  { id: "devtools", label: "Installing developer tools" },
  { id: "images", label: "Downloading container images" },
  { id: "offline", label: "Configuring offline mode" },
  { id: "amd-tuning", label: "Tuning AMD GPU settings" },
  { id: "services", label: "Starting services" },
  { id: "health", label: "Checking service health" },
  { id: "summary", label: "Finishing up" },
  { id: "complete", label: "Done" },
];

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

  // -1 for a phase the installer skipped past or one this build does not know;
  // the dots then stay dark and the label falls through to whatever the
  // installer last said, which is the same as the old unknown-phase behaviour.
  const currentIdx = PHASES.findIndex((p) => p.id === progress.phase);
  const phaseLabel =
    PHASES[currentIdx]?.label || progress.message || "Working...";

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
        {PHASES.map((phase, idx) => (
          <div
            key={phase.id}
            // Index order, not equality, so a phase the run skips — offline
            // and amd-tuning do not fire on every host — still reads as done
            // once the install is past it.
            className={`w-2 h-2 rounded-full transition-colors ${
              idx < currentIdx
                ? "bg-ods-500"
                : idx === currentIdx
                  ? "bg-ods-400 animate-pulse"
                  : "bg-gray-700"
            }`}
            title={phase.label}
          />
        ))}
      </div>

      <p className="mt-10 text-xs text-gray-600 text-center max-w-sm">
        Please don't close this window. If the install is interrupted, you can
        re-run the installer and it will resume where it left off.
      </p>
    </div>
  );
}

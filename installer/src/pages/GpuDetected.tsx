import { useEffect, useState } from "react";
import Button from "../components/Button";
import StatusIcon from "../components/StatusIcon";
import { detectGpu, type GpuResult } from "../hooks/useTauri";

interface Props {
  onNext: (tier: number) => void;
}

const VENDOR_LABELS: Record<string, string> = {
  nvidia: "NVIDIA",
  amd: "AMD Radeon",
  intel: "Intel Arc",
  apple: "Apple Silicon",
  none: "No dedicated GPU",
};

export default function GpuDetected({ onNext }: Props) {
  const [loading, setLoading] = useState(true);
  const [result, setResult] = useState<GpuResult | null>(null);
  const [selectedTier, setSelectedTier] = useState<number>(1);

  useEffect(() => {
    detectGpu().then((r) => {
      setResult(r);
      setSelectedTier(r.recommended_tier);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <StatusIcon status="loading" />
        <p className="mt-4 text-gray-400">Detecting GPU hardware...</p>
      </div>
    );
  }

  if (!result) return null;

  const { gpu } = result;
  const hasGpu = gpu.vendor !== "none";
  const vramDisplay =
    gpu.vram_mb > 0
      ? gpu.vram_mb >= 1024
        ? `${(gpu.vram_mb / 1024).toFixed(0)} GB VRAM`
        : `${gpu.vram_mb} MB VRAM`
      : null;

  return (
    <div className="flex flex-col items-center justify-center h-full px-8">
      <h2 className="text-2xl font-bold mb-2">GPU Detected</h2>

      {/* GPU Card */}
      <div className="bg-gray-900 rounded-xl p-6 mb-6 w-full max-w-md text-center">
        <p className="text-xs text-ods-400 uppercase tracking-wider mb-2">
          {VENDOR_LABELS[gpu.vendor] || gpu.vendor}
        </p>
        <p className="text-xl font-semibold text-white mb-1">{gpu.name}</p>
        {vramDisplay && (
          <p className="text-sm text-gray-400">{vramDisplay}</p>
        )}
        {gpu.driver_version && (
          <p className="text-xs text-gray-600 mt-1">
            Driver {gpu.driver_version}
          </p>
        )}
      </div>

      {/* Tier recommendation */}
      <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-5 mb-6 w-full max-w-md">
        <p className="text-sm text-gray-400 mb-3">Recommended configuration:</p>
        <p className="text-white font-medium">{result.tier_description}</p>
      </div>

      {/* Tier override */}
      {hasGpu && (
        <div className="mb-6 w-full max-w-md">
          <p className="text-xs text-gray-500 mb-2 text-center">
            Or choose a different tier:
          </p>
          <div className="flex gap-2 justify-center">
            {[1, 2, 3, 4].map((t) => (
              <button
                key={t}
                onClick={() => setSelectedTier(t)}
                className={`px-4 py-2 rounded-lg text-sm transition-colors ${
                  selectedTier === t
                    ? "bg-ods-600 text-white"
                    : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                }`}
              >
                Tier {t}
              </button>
            ))}
          </div>
        </div>
      )}

      {!hasGpu && (
        <p className="text-sm text-gray-500 mb-6 text-center max-w-sm">
          No dedicated GPU detected. ODS will use cloud AI providers
          instead of local inference.
        </p>
      )}

      <Button onClick={() => onNext(hasGpu ? selectedTier : 0)}>
        Continue
      </Button>
    </div>
  );
}

import { useEffect, useState } from "react";
import Button from "../components/Button";
import StatusIcon from "../components/StatusIcon";
import { checkSystem, type SystemCheckResult, type RequirementCheck } from "../hooks/useTauri";

interface Props {
  onNext: () => void;
  onError: (msg: string) => void;
}

export default function SystemCheck({ onNext, onError }: Props) {
  const [loading, setLoading] = useState(true);
  const [result, setResult] = useState<SystemCheckResult | null>(null);

  useEffect(() => {
    checkSystem()
      .then((r) => {
        setResult(r);
        setLoading(false);
      })
      .catch((e) => {
        onError(String(e));
      });
  }, [onError]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-full py-8">
        <StatusIcon status="loading" />
        <p className="mt-4 text-gray-400">Checking your system...</p>
      </div>
    );
  }

  if (!result) return null;

  const allMet = result.requirements.every((r: RequirementCheck) => r.met);

  return (
    <div className="flex flex-col items-center justify-center min-h-full py-8 px-8">
      <h2 className="text-2xl font-bold mb-2">System Check</h2>
      <p className="text-gray-400 mb-8">
        {result.system.os_version} &middot; {result.system.arch}
      </p>

      <div className="w-full max-w-md space-y-3 mb-8">
        {result.requirements.map((req: RequirementCheck) => (
          <div
            key={req.name}
            className="flex items-center justify-between bg-gray-900 rounded-lg px-4 py-3"
          >
            <div className="flex items-center gap-3">
              <StatusIcon status={req.met ? "pass" : "fail"} />
              <div>
                <p className="text-sm font-medium text-white">{req.name}</p>
                <p className="text-xs text-gray-500">{req.found}</p>
              </div>
            </div>
            <span className="text-xs text-gray-600">{req.required}</span>
          </div>
        ))}

        {/* Docker status */}
        <div className="flex items-center justify-between bg-gray-900 rounded-lg px-4 py-3">
          <div className="flex items-center gap-3">
            <StatusIcon
              status={
                result.docker.installed && result.docker.running
                  ? "pass"
                  : result.docker.installed
                    ? "warn"
                    : "fail"
              }
            />
            <div>
              <p className="text-sm font-medium text-white">Docker</p>
              <p className="text-xs text-gray-500">
                {result.docker.installed
                  ? result.docker.running
                    ? result.docker.version
                    : "Installed but not running"
                  : "Not installed"}
              </p>
            </div>
          </div>
          <span className="text-xs text-gray-600">Required</span>
        </div>
      </div>

      <div className="flex gap-3">
        <Button onClick={onNext}>
          {allMet && result.docker.installed
            ? "Continue"
            : "Continue Anyway"}
        </Button>
      </div>

      {!allMet && (
        <p className="mt-4 text-xs text-yellow-500/70">
          Some requirements aren't met. We'll try to fix them in the next step.
        </p>
      )}
    </div>
  );
}

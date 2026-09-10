import { useEffect, useState } from "react";
import Button from "../components/Button";
import StatusIcon from "../components/StatusIcon";
import {
  checkPrerequisites,
  installPrerequisite,
  type PrerequisiteStatus,
} from "../hooks/useTauri";

interface Props {
  onNext: () => void;
  onError: (msg: string) => void;
}

type InstallStatus = "idle" | "installing" | "done" | "failed";

// A failed attempt has to keep offering the button. Gating it on "idle" alone
// meant the one transient failure — a dropped download, a declined UAC prompt
// — removed the only way to try again, leaving a red row with no affordance
// and Continue still disabled. Re-check cannot help: nothing got installed, so
// it just confirms the same missing prerequisite.
const canAttempt = (status: InstallStatus) =>
  status === "idle" || status === "failed";

export default function Prerequisites({ onNext, onError }: Props) {
  const [prereqs, setPrereqs] = useState<PrerequisiteStatus | null>(null);
  const [dockerStatus, setDockerStatus] = useState<InstallStatus>("idle");
  const [wslStatus, setWslStatus] = useState<InstallStatus>("idle");
  const [message, setMessage] = useState("");
  const [rebootNeeded, setRebootNeeded] = useState(false);

  useEffect(() => {
    checkPrerequisites()
      .then(setPrereqs)
      .catch((e) => onError(String(e)));
  }, [onError]);

  if (!prereqs) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <StatusIcon status="loading" />
        <p className="mt-4 text-gray-400">Checking prerequisites...</p>
      </div>
    );
  }

  if (prereqs.all_met) {
    // All good, auto-advance
    return (
      <div className="flex flex-col items-center justify-center h-full px-8">
        <h2 className="text-2xl font-bold mb-4">All Prerequisites Met</h2>
        <div className="space-y-2 mb-8">
          <div className="flex items-center gap-3">
            <StatusIcon status="pass" />
            <span className="text-gray-300">Git</span>
          </div>
          <div className="flex items-center gap-3">
            <StatusIcon status="pass" />
            <span className="text-gray-300">Docker</span>
          </div>
          {prereqs.wsl2_needed && (
            <div className="flex items-center gap-3">
              <StatusIcon status="pass" />
              <span className="text-gray-300">WSL2</span>
            </div>
          )}
        </div>
        <Button onClick={onNext}>Continue</Button>
      </div>
    );
  }

  const refresh = async () => {
    const updated = await checkPrerequisites();
    setPrereqs(updated);
  };

  const handleInstallDocker = async () => {
    setDockerStatus("installing");
    setMessage("Installing Docker... this may take a few minutes.");
    try {
      const result = await installPrerequisite("docker");
      if (result.success) {
        setDockerStatus("done");
        setMessage(result.message);
        // Nothing re-read the prerequisites after a success, so the row kept
        // its failure icon and Continue stayed disabled until the user
        // happened to press Re-check. Deliberately outside the try below: a
        // re-check that fails is not a failed install.
        refresh().catch(() => setMessage(`${result.message} Press Re-check.`));
      } else {
        setDockerStatus("failed");
        setMessage(result.message);
      }
    } catch (e) {
      setDockerStatus("failed");
      setMessage(String(e));
    }
  };

  const handleInstallWSL = async () => {
    setWslStatus("installing");
    setMessage("Installing WSL2... this may take a few minutes.");
    try {
      const result = await installPrerequisite("wsl2");
      if (result.success) {
        setWslStatus("done");
        setMessage(result.message);
        if (result.reboot_required) {
          setRebootNeeded(true);
        } else {
          refresh().catch(() =>
            setMessage(`${result.message} Press Re-check.`),
          );
        }
      } else {
        setWslStatus("failed");
        setMessage(result.message);
      }
    } catch (e) {
      setWslStatus("failed");
      setMessage(String(e));
    }
  };

  return (
    <div className="flex flex-col items-center justify-center h-full px-8">
      <h2 className="text-2xl font-bold mb-2">Prerequisites Needed</h2>
      <p className="text-gray-400 mb-8 text-center max-w-md">
        A few things need to be set up before we can install ODS.
      </p>

      <div className="w-full max-w-md space-y-4 mb-8">
        {/* Git */}
        <div className="flex items-center justify-between bg-gray-900 rounded-lg px-4 py-3">
          <div className="flex items-center gap-3">
            <StatusIcon status={prereqs.git_installed ? "pass" : "fail"} />
            <span className="text-sm text-white">Git</span>
          </div>
          {!prereqs.git_installed && (
            <span className="text-xs text-gray-500">
              Install from git-scm.com
            </span>
          )}
        </div>

        {/* WSL2 (Windows only) */}
        {prereqs.wsl2_needed && (
          <div className="flex items-center justify-between bg-gray-900 rounded-lg px-4 py-3">
            <div className="flex items-center gap-3">
              <StatusIcon
                status={
                  prereqs.wsl2_installed
                    ? "pass"
                    : wslStatus === "installing"
                      ? "loading"
                      : "fail"
                }
              />
              <span className="text-sm text-white">WSL2</span>
            </div>
            {!prereqs.wsl2_installed && canAttempt(wslStatus) && (
              <Button variant="secondary" onClick={handleInstallWSL}>
                {wslStatus === "failed" ? "Retry" : "Install"}
              </Button>
            )}
          </div>
        )}

        {/* Docker */}
        <div className="flex items-center justify-between bg-gray-900 rounded-lg px-4 py-3">
          <div className="flex items-center gap-3">
            <StatusIcon
              status={
                prereqs.docker_installed && prereqs.docker_running
                  ? "pass"
                  : dockerStatus === "installing"
                    ? "loading"
                    : "fail"
              }
            />
            <div>
              <p className="text-sm text-white">Docker</p>
              {prereqs.docker_installed && !prereqs.docker_running && (
                <p className="text-xs text-yellow-500">
                  Docker is installed but not running. Please start it.
                </p>
              )}
            </div>
          </div>
          {!prereqs.docker_installed && canAttempt(dockerStatus) && (
            <Button variant="secondary" onClick={handleInstallDocker}>
              {dockerStatus === "failed" ? "Retry" : "Install"}
            </Button>
          )}
        </div>
      </div>

      {message && (
        <p className="text-sm text-gray-400 mb-4 text-center max-w-md">
          {message}
        </p>
      )}

      {rebootNeeded ? (
        <div className="text-center">
          <p className="text-yellow-400 mb-4">
            A restart is needed to finish WSL2 setup. After restarting, run this
            installer again — it will pick up where it left off.
          </p>
          <Button variant="secondary" onClick={() => window.close()}>
            Close &amp; Restart Later
          </Button>
        </div>
      ) : (
        <div className="flex gap-3">
          <Button variant="ghost" onClick={refresh}>
            Re-check
          </Button>
          <Button
            onClick={onNext}
            disabled={
              !prereqs.git_installed ||
              !prereqs.docker_installed ||
              !prereqs.docker_running
            }
          >
            Continue
          </Button>
        </div>
      )}
    </div>
  );
}

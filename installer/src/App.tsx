import { useState } from "react";
import Welcome from "./pages/Welcome";
import SystemCheck from "./pages/SystemCheck";
import Prerequisites from "./pages/Prerequisites";
import GpuDetected from "./pages/GpuDetected";
import Features from "./pages/Features";
import Installing from "./pages/Installing";
import Complete from "./pages/Complete";
import ErrorPage from "./pages/ErrorPage";

export type WizardStep =
  | "welcome"
  | "system_check"
  | "prerequisites"
  | "gpu"
  | "features"
  | "installing"
  | "complete"
  | "error";

export interface WizardState {
  tier: number;
  features: string[];
  installDir?: string;
  error?: string;
}

const STEPS: WizardStep[] = [
  "welcome",
  "system_check",
  "prerequisites",
  "gpu",
  "features",
  "installing",
  "complete",
];

export default function App() {
  const [step, setStep] = useState<WizardStep>("welcome");
  const [state, setState] = useState<WizardState>({
    tier: 1,
    features: [],
  });

  const stepIndex = STEPS.indexOf(step);
  const progress =
    step === "error" ? 0 : Math.round((stepIndex / (STEPS.length - 1)) * 100);

  const goTo = (s: WizardStep) => setStep(s);
  const update = (partial: Partial<WizardState>) =>
    setState((prev) => ({ ...prev, ...partial }));

  return (
    <div className="flex flex-col h-screen bg-gray-950">
      {/* Progress bar */}
      {step !== "welcome" && step !== "error" && (
        <div className="h-1 bg-gray-800">
          <div
            className="h-full bg-ods-500 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {step === "welcome" && <Welcome onNext={() => goTo("system_check")} />}
        {step === "system_check" && (
          <SystemCheck
            onNext={() => goTo("prerequisites")}
            onError={(msg) => {
              update({ error: msg });
              goTo("error");
            }}
          />
        )}
        {step === "prerequisites" && (
          <Prerequisites
            onNext={() => goTo("gpu")}
            onError={(msg) => {
              update({ error: msg });
              goTo("error");
            }}
          />
        )}
        {step === "gpu" && (
          <GpuDetected
            onNext={(tier: number) => {
              update({ tier });
              goTo("features");
            }}
          />
        )}
        {step === "features" && (
          <Features
            onNext={(features: string[]) => {
              update({ features });
              goTo("installing");
            }}
          />
        )}
        {step === "installing" && (
          <Installing
            tier={state.tier}
            features={state.features}
            installDir={state.installDir}
            onComplete={() => goTo("complete")}
            onError={(msg) => {
              update({ error: msg });
              goTo("error");
            }}
          />
        )}
        {step === "complete" && <Complete />}
        {step === "error" && (
          <ErrorPage
            message={state.error || "An unknown error occurred."}
            onRetry={() => goTo("system_check")}
          />
        )}
      </div>
    </div>
  );
}

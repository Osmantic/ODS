import Button from "../components/Button";

interface Props {
  onNext: () => void;
}

export default function Welcome({ onNext }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className="mb-8">
        <div className="text-6xl mb-4">&#9729;</div>
        <h1 className="text-4xl font-bold text-white mb-3">ODS</h1>
        <p className="text-lg text-gray-400 max-w-md">
          Local AI anywhere, for everyone. Chat, voice, agents, image
          generation, and more — running entirely on your machine.
        </p>
      </div>

      <div className="space-y-3 mb-10 text-sm text-gray-500 max-w-sm">
        <div className="flex items-center gap-3">
          <span className="text-ods-400">&#9679;</span>
          <span>No cloud accounts or subscriptions needed</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-ods-400">&#9679;</span>
          <span>Your data stays on your machine</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-ods-400">&#9679;</span>
          <span>Works offline once installed</span>
        </div>
      </div>

      <Button onClick={onNext} className="px-10 py-4 text-base">
        Get Started
      </Button>

      <p className="mt-6 text-xs text-gray-600">
        This will check your system and guide you through setup.
      </p>
    </div>
  );
}

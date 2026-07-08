import { useState } from "react";
import Button from "../components/Button";

interface Props {
  onNext: (features: string[]) => void;
}

interface FeatureOption {
  id: string;
  name: string;
  description: string;
  default: boolean;
  vramNote?: string;
}

const FEATURES: FeatureOption[] = [
  {
    id: "chat",
    name: "Chat & LLM",
    description:
      "AI chat interface with a powerful language model running locally.",
    default: true,
  },
  {
    id: "voice",
    name: "Voice",
    description: "Speech-to-text and text-to-speech for voice conversations.",
    default: false,
    vramNote: "Adds ~1GB VRAM usage",
  },
  {
    id: "workflows",
    name: "Workflows & Agents",
    description:
      "n8n workflow automation and OpenClaw AI agents for complex tasks.",
    default: false,
  },
  {
    id: "rag",
    name: "Knowledge Base (RAG)",
    description:
      "Vector search with Qdrant for retrieval-augmented generation.",
    default: false,
  },
  {
    id: "image_gen",
    name: "Image Generation",
    description: "Generate images locally with ComfyUI and FLUX.",
    default: false,
    vramNote: "Requires 8GB+ VRAM",
  },
  {
    id: "search",
    name: "Private Search",
    description:
      "Self-hosted search engine (SearXNG) with no tracking or ads.",
    default: false,
  },
];

export default function Features({ onNext }: Props) {
  const [selected, setSelected] = useState<Set<string>>(
    new Set(FEATURES.filter((f) => f.default).map((f) => f.id)),
  );

  const toggle = (id: string) => {
    // Chat is always enabled
    if (id === "chat") return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    setSelected(new Set(FEATURES.map((f) => f.id)));
  };

  return (
    <div className="flex flex-col items-center justify-center h-full px-8">
      <h2 className="text-2xl font-bold mb-2">Choose Features</h2>
      <p className="text-gray-400 mb-6 text-center max-w-md">
        Select which capabilities to install. You can always enable more later.
      </p>

      <div className="w-full max-w-md space-y-2 mb-6">
        {FEATURES.map((feature) => {
          const isSelected = selected.has(feature.id);
          const isRequired = feature.id === "chat";
          return (
            <button
              key={feature.id}
              onClick={() => toggle(feature.id)}
              className={`w-full text-left rounded-lg px-4 py-3 transition-colors border ${
                isSelected
                  ? "bg-ods-600/10 border-ods-600/50"
                  : "bg-gray-900 border-gray-800 hover:border-gray-700"
              }`}
            >
              <div className="flex items-start gap-3">
                <div
                  className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center text-xs ${
                    isSelected
                      ? "bg-ods-600 border-ods-600 text-white"
                      : "border-gray-600"
                  } ${isRequired ? "opacity-50" : ""}`}
                >
                  {isSelected && "&#10003;"}
                </div>
                <div className="flex-1">
                  <p className="text-sm font-medium text-white">
                    {feature.name}
                    {isRequired && (
                      <span className="ml-2 text-xs text-gray-500">
                        (always included)
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {feature.description}
                  </p>
                  {feature.vramNote && (
                    <p className="text-xs text-yellow-600 mt-1">
                      {feature.vramNote}
                    </p>
                  )}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <div className="flex gap-3">
        <Button variant="ghost" onClick={selectAll}>
          Select All
        </Button>
        <Button onClick={() => onNext(Array.from(selected))}>
          Install
        </Button>
      </div>
    </div>
  );
}

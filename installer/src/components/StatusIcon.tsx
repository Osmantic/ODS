interface StatusIconProps {
  status: "pass" | "fail" | "loading" | "warn";
}

export default function StatusIcon({ status }: StatusIconProps) {
  switch (status) {
    case "pass":
      return (
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-green-500/20 text-green-400 text-sm">
          &#10003;
        </span>
      );
    case "fail":
      return (
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-red-500/20 text-red-400 text-sm">
          &#10007;
        </span>
      );
    case "warn":
      return (
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-yellow-500/20 text-yellow-400 text-sm">
          !
        </span>
      );
    case "loading":
      return (
        <span className="inline-flex items-center justify-center w-6 h-6">
          <span className="w-4 h-4 border-2 border-ods-400 border-t-transparent rounded-full animate-spin" />
        </span>
      );
  }
}

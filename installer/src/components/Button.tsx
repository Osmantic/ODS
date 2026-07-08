interface ButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  className?: string;
}

export default function Button({
  children,
  onClick,
  variant = "primary",
  disabled = false,
  className = "",
}: ButtonProps) {
  const base =
    "px-6 py-3 rounded-lg font-medium transition-all duration-200 text-sm";
  const variants = {
    primary:
      "bg-ods-600 hover:bg-ods-500 text-white disabled:bg-gray-700 disabled:text-gray-400",
    secondary:
      "bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700",
    ghost: "text-gray-400 hover:text-white hover:bg-gray-800/50",
  };

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

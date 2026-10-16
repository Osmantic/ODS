// Shared by conversation prompts and clarification tools, including compact routes.
export const RESPONSE_LANGUAGE_CONTRACT =
  "For replies, clarification questions and options, honor the owner's explicit response-language request; otherwise match the current owner message. If its language is unclear, default to English. Do not infer language from names, paths, locale, quoted text, tool output or examples. Preserve explicitly requested languages for generated content.";

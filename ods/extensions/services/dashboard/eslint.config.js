import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  js.configs.recommended,
  {
    files: ["**/*.{js,jsx}"],
    plugins: {
      react,
      "react-hooks": reactHooks,
    },
    settings: {
      react: { version: "detect" },
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        // Browser globals
        document: "readonly",
        window: "readonly",
        navigator: "readonly",
        console: "readonly",
        fetch: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        AbortController: "readonly",
        AbortSignal: "readonly",
        Event: "readonly",
        EventTarget: "readonly",
        MessageEvent: "readonly",
        CloseEvent: "readonly",
        WebSocket: "readonly",
        CustomEvent: "readonly",
        MouseEvent: "readonly",
        StorageEvent: "readonly",
        Storage: "readonly",
        File: "readonly",
        HTMLElement: "readonly",
        HTMLCanvasElement: "readonly",
        HTMLDialogElement: "readonly",
        HTMLAnchorElement: "readonly",
        Image: "readonly",
        Node: "readonly",
        Blob: "readonly",
        Audio: "readonly",
        FormData: "readonly",
        MediaRecorder: "readonly",
        createImageBitmap: "readonly",
        crypto: "readonly",
        MutationObserver: "readonly",
        ResizeObserver: "readonly",
        IntersectionObserver: "readonly",
        requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly",
        TextDecoder: "readonly",
        TextEncoder: "readonly",
        confirm: "readonly",
        alert: "readonly",
        prompt: "readonly",
        performance: "readonly",
        // Vite
        process: "readonly",
      },
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      // Mark JSX identifiers as used — without this, no-unused-vars reports
      // every component import that is only referenced inside JSX (~700
      // false-positive warnings that drown out real ones).
      "react/jsx-uses-vars": "warn",
      // Conditional/misnested hook calls corrupt React state; gate on them.
      // exhaustive-deps stays a warning: some effects intentionally take a
      // narrower dep set (mount-once listeners, debounced reloads).
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
  {
    files: ["**/*.test.{js,jsx}", "**/__tests__/**/*.{js,jsx}"],
    languageOptions: {
      globals: {
        describe: "readonly",
        test: "readonly",
        expect: "readonly",
        it: "readonly",
        vi: "readonly",
        beforeEach: "readonly",
        afterEach: "readonly",
        beforeAll: "readonly",
        afterAll: "readonly",
      },
    },
  },
  {
    ignores: ["dist/**", "node_modules/**"],
  },
];

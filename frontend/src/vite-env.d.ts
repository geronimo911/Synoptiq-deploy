/// <reference types="vite/client" />

// Vite's client types declare `import.meta.env` as an index signature, and the
// project enables `noPropertyAccessFromIndexSignature`, so dot access to a
// specific variable is a type error unless the key is declared here. Declaring
// the two public frontend variables keeps `import.meta.env.VITE_API_BASE_URL`
// and `import.meta.env.VITE_APP_MODE` type-safe. No secrets belong in this file:
// only the public Vite variables the browser is allowed to see.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_APP_MODE?: string;
}

/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Read-only bearer token; never an ops token (MVP 5 / SP 5.4). */
  readonly VITE_HARBOR_API_TOKEN?: string;
  /** API origin prefix; empty means same-origin (the dev proxy handles it). */
  readonly VITE_HARBOR_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

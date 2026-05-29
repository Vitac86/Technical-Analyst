/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Optional default BCS refresh token bundled at build time from `.env.local`.
   * Used only as a fallback when the user has not entered a session token in
   * Settings. Never written to localStorage, IndexedDB, or any log.
   */
  readonly VITE_DEFAULT_BCS_REFRESH_TOKEN?: string;
  /**
   * Optional OAuth client id for the bundled BCS refresh token.
   * Defaults to trade-api-read when omitted or invalid.
   */
  readonly VITE_DEFAULT_BCS_CLIENT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

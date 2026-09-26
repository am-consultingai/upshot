/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" builds the first-run setup mock into the bundle (Setup 0). Never set for a release. */
  readonly VITE_SETUP_MOCK?: string;
}

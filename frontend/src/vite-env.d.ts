/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_EXACT_API_PROXY_TARGET?: string;
  readonly VITE_PROMOP_LOCAL_TARGET?: string;
  readonly VITE_PROMOP_STAGING_TARGET?: string;
  readonly VITE_PROMOP_BASE?: string;
  readonly VITE_EXACT_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

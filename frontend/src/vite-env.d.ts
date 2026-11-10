/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_EXACT_API_PROXY_TARGET?: string;
  readonly VITE_CTOMOP_LOCAL_TARGET?: string;
  readonly VITE_CTOMOP_STAGING_TARGET?: string;
  readonly VITE_CTOMOP_BASE?: string;
  readonly VITE_EXACT_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

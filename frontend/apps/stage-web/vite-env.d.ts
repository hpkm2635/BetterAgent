/// <reference types="vite/client" />
/// <reference types="../../vite-env.d.ts" />

interface ImportMetaEnv {
  readonly VITE_APP_TARGET_HUGGINGFACE_SPACE: string
  /** Shared secret required by the Go core's WebGateway /ws handshake (WEBGATEWAY_TOKEN). See .env.example. */
  readonly VITE_BETTERAGENT_WS_TOKEN: string
  // more env variables...
}

/// <reference types="vite/client" />

import type { KalshiBotApi } from '../shared/types';

declare global {
  interface Window {
    kbot: KalshiBotApi;
  }
}

export {};

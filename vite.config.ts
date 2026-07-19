import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import electron from 'vite-plugin-electron';
import renderer from 'vite-plugin-electron-renderer';

// Vite drives the React renderer and, via vite-plugin-electron, compiles the
// Electron main + preload into dist-electron and launches Electron in dev.

export default defineConfig({
  plugins: [
    react(),
    electron([
      {
        entry: 'electron/main.ts',
        vite: { build: { outDir: 'dist-electron', rollupOptions: { external: ['electron'] } } },
      },
      {
        entry: 'electron/preload.ts',
        onstart: (opts) => opts.reload(),
        vite: { build: { outDir: 'dist-electron', rollupOptions: { external: ['electron'] } } },
      },
    ]),
    renderer(),
  ],
  build: { outDir: 'dist' },
  clearScreen: false,
});

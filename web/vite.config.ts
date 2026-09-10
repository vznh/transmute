import { sites } from '@openai/sites-vite-plugin';
import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';

export default defineConfig({
  css: { postcss: { plugins: [tailwindcss()] } },
  optimizeDeps: { exclude: ['@ffmpeg/ffmpeg'] },
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    allowedHosts: process.env.TRANSMUTE_PREVIEW_HOST ? [process.env.TRANSMUTE_PREVIEW_HOST] : [],
    watch: { useFsEvents: false, usePolling: true },
    proxy: { '/api': { target: 'http://127.0.0.1:8766', changeOrigin: true, timeout: 180000, proxyTimeout: 180000 } },
  },
  plugins: [vinext(), sites()],
});

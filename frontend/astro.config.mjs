import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

export default defineConfig({
  site: 'https://nailong.fall-star.top',
  integrations: [react()],
  devToolbar: { enabled: false },
  vite: {
    server: {
      proxy: {
        '/api/': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  },
});

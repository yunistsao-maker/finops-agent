import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_');
  // VITE_LOCAL_AGENT=true: `npm run dev` proxies to the agent running locally (python main.py) - see README
  const local = env.VITE_LOCAL_AGENT_URL || 'http://localhost:8080';
  return {
    plugins: [react()],
    build: { chunkSizeWarningLimit: 1500 },
    server: { proxy: { '/invocations': local, '/ping': local } },
  };
});

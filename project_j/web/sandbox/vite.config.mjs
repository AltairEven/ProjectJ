// Classroom sandbox and page preview, kept for quick UI debugging (not part of the app build).
// npm run --prefix project_j/web sandbox → http://127.0.0.1:5174/ (the class) and /app.html (the pages).
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default {
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5174, strictPort: true, fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] } },
  cacheDir: fileURLToPath(new URL('../node_modules/.vite-sandbox', import.meta.url)),
}

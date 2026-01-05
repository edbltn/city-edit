/**
 * Bun static file server.
 * Run with: bun server.ts
 */

const PORT = 3000;
const API_HOST = process.env.API_HOST || "http://localhost:5001";

const server = Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);
    const path = url.pathname;

    // Proxy API requests to Flask backend
    if (path.startsWith("/api/") || path === "/ws") {
      const targetUrl = `${API_HOST}${path}${url.search}`;
      return fetch(targetUrl, {
        method: req.method,
        headers: req.headers,
        body: req.body,
      });
    }

    // Serve static files
    let filePath = path === "/" ? "/index.html" : path;
    const file = Bun.file(import.meta.dir + filePath);

    if (await file.exists()) {
      return new Response(file);
    }

    // 404 fallback
    return new Response("Not Found", { status: 404 });
  },
});

console.log(`Server running at http://localhost:${PORT}`);
console.log(`Proxying API requests to ${API_HOST}`);

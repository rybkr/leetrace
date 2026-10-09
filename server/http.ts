import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { extname, resolve, sep } from 'node:path';
import { createHTTPHandler } from '@trpc/server/adapters/standalone';
import type { AppRouter } from './router.js';

const contentTypes: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
  '.woff2': 'font/woff2', '.ttf': 'font/ttf',
};

export function createAppServer(router: AppRouter, directory = resolve('frontend/dist')) {
  const rpc = createHTTPHandler({
    router,
    basePath: '/trpc/',
    maxBodySize: 1_000_000,
    createContext: ({ req }) => {
      const authorization = req.headers.authorization;
      return { token: authorization?.startsWith('Bearer ') ? authorization.slice(7) : undefined };
    },
  });
  return createServer((request, response) => {
    response.setHeader('X-Content-Type-Options', 'nosniff');
    let pathname: string;
    try {
      pathname = new URL(request.url ?? '/', 'http://localhost').pathname;
    } catch {
      response.writeHead(400);
      response.end();
      return;
    }
    if (pathname.startsWith('/trpc/')) {
      response.setHeader('Cache-Control', 'no-store');
      rpc(request, response);
      return;
    }
    if (pathname === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end('{"ok":true}');
      return;
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      response.writeHead(405);
      response.end();
      return;
    }
    void (async () => {
      let file: string;
      try {
        file = resolve(directory, `.${decodeURIComponent(pathname)}`);
      } catch {
        response.writeHead(400);
        response.end();
        return;
      }
      if (!file.startsWith(`${resolve(directory)}${sep}`)) file = resolve(directory, 'index.html');
      try {
        const information = await stat(file).catch(() => null);
        if (!information?.isFile()) {
          if (extname(pathname)) {
            response.writeHead(404);
            response.end('Not found');
            return;
          }
          file = resolve(directory, 'index.html');
          await stat(file);
        }
        response.writeHead(200, {
          'Content-Type': contentTypes[extname(file)] ?? 'application/octet-stream',
          'Cache-Control': file.includes(`${sep}assets${sep}`) ? 'public, max-age=31536000, immutable' : 'no-cache',
        });
        if (request.method === 'HEAD') response.end();
        else createReadStream(file).on('error', () => response.destroy()).pipe(response);
      } catch {
        response.writeHead(404);
        response.end('Frontend build missing. Run pnpm build.');
      }
    })();
  });
}

import { createAppServer } from './http.js';
import { appRouter, rooms } from './router.js';

const port = Number(process.env.BACKEND_PORT ?? process.env.PORT ?? 8000);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('BACKEND_PORT or PORT must be between 1 and 65535.');
const server = createAppServer(appRouter);
const ticker = setInterval(() => rooms.tick(), 1000);
ticker.unref();
server.listen(port, '0.0.0.0', () => console.log(`LeetRace listening on http://localhost:${port}`));

function shutdown() {
  clearInterval(ticker);
  server.close(() => process.exit(0));
  server.closeAllConnections();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

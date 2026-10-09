import { createTRPCClient, httpBatchLink } from '@trpc/client';
import { afterEach, describe, expect, it } from 'vitest';
import { createAppServer } from '../server/http.js';
import { ProblemRepository, type Problem } from '../server/problems.js';
import { createRouter, type AppRouter, type RoomSnapshot } from '../server/router.js';
import { RoomStore } from '../server/rooms.js';

const problem: Problem = {
  id: 'sum', title: 'Sum', difficulty: 'Easy', statement: 'Return the input.',
  entryPoint: 'Solution().echo', starterCode: 'class Solution:\n    def echo(self, value: int) -> int:\n        pass\n',
  parameters: [{ name: 'value', type: 'int' }], returnType: 'int', tests: [{ input: [1], expected: 1 }],
};
const cleanup: (() => void)[] = [];
afterEach(() => { for (const stop of cleanup.splice(0)) stop(); });

async function serverSetup() {
  const store = new RoomStore(() => problem);
  const router = createRouter(store, new ProblemRepository());
  const server = createAppServer(router);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  cleanup.push(() => { server.closeAllConnections(); server.close(); });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Missing server address');
  const url = `http://127.0.0.1:${address.port}/trpc`;
  const client = (token?: string) => createTRPCClient<AppRouter>({ links: [
    httpBatchLink({ url, headers: () => token ? { authorization: `Bearer ${token}` } : {} }),
  ] });
  return { store, client, url };
}

describe('tRPC transport', () => {
  it('limits trimmed player names to twelve characters when creating or joining a room', async () => {
    const { client } = await serverSetup();
    const host = await client().createRoom.mutate({ name: '  abcdefghijkl  ' });
    expect(host.name).toBe('abcdefghijkl');
    await expect(client().createRoom.mutate({ name: 'abcdefghijklm' })).rejects.toThrow('at most 12 characters');
    await expect(client().joinRoom.mutate({ roomId: host.roomId, name: 'abcdefghijklm' })).rejects.toThrow('at most 12 characters');
    const guest = await client().joinRoom.mutate({ roomId: host.roomId, name: '  mnopqrstuvwx  ' });
    expect(guest.name).toBe('mnopqrstuvwx');
  });

  it('validates inputs and authenticates room access over HTTP', async () => {
    const { client } = await serverSetup();
    await expect(client().createRoom.mutate({ name: 'Dark', timeLimit: 1 })).rejects.toThrow();
    const host = await client().createRoom.mutate({ name: 'Dark' });
    await expect(client().room.query({ roomId: host.roomId })).rejects.toThrow('Rejoin');
    const snapshot = await client(host.token).room.query({ roomId: host.roomId });
    expect(snapshot.host).toBe('Dark');
    expect(snapshot.problem).toBeNull();
    await client(host.token).start.mutate({ roomId: host.roomId });
    const playing = await client(host.token).room.query({ roomId: host.roomId });
    expect(playing.problem?.starterCode).toContain('class Solution:');
    const result = await client(host.token).submit.mutate({
      roomId: host.roomId, code: 'class Solution:\n    def echo(self, value):\n        return value',
    });
    expect(result).toMatchObject({ passed: 1, solved: true, error: null });
  });

  it('sends an immediate personalized SSE snapshot then live changes', async () => {
    const { client, url } = await serverSetup();
    const host = await client().createRoom.mutate({ name: 'Dark' });
    const abort = new AbortController();
    cleanup.push(() => abort.abort());
    const input = encodeURIComponent(JSON.stringify({ roomId: host.roomId, token: host.token }));
    const response = await fetch(`${url}/roomUpdates?input=${input}`, { signal: abort.signal });
    expect(response.headers.get('content-type')).toContain('text/event-stream');
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Missing SSE stream');
    const decoder = new TextDecoder();
    let buffer = '';
    const nextSnapshot = async (): Promise<RoomSnapshot> => {
      while (true) {
        const boundary = buffer.indexOf('\n\n');
        if (boundary !== -1) {
          const event = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (!event.startsWith('event:')) {
            const data = event.split('\n').find((line) => line.startsWith('data: '));
            if (data) return JSON.parse(data.slice(6)) as RoomSnapshot;
          }
          continue;
        }
        const chunk = await reader.read();
        if (chunk.done) throw new Error('SSE stream ended');
        buffer += decoder.decode(chunk.value, { stream: true });
      }
    };
    expect((await nextSnapshot()).me.name).toBe('Dark');
    await client(host.token).start.mutate({ roomId: host.roomId });
    const playing = await nextSnapshot();
    expect(playing.state).toBe('playing');
    expect(playing.problem).not.toHaveProperty('tests');
    abort.abort();
  });
});

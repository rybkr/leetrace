import { initTRPC, type inferRouterInputs, type inferRouterOutputs } from '@trpc/server';
import { z } from 'zod';
import { difficultySchema, ProblemRepository } from './problems.js';
import { RoomStore } from './rooms.js';

export interface Context { token: string | undefined }
const t = initTRPC.context<Context>().create({ sse: { ping: { enabled: true, intervalMs: 15_000 } } });
const roomInput = z.object({ roomId: z.string().trim().toUpperCase().regex(/^[A-F0-9]{6}$/) });
const name = z.string().trim().min(1).max(12, 'Use at most 12 characters for your username.');

export function createRouter(store: RoomStore, problems: ProblemRepository) {
  return t.router({
    createRoom: t.procedure.input(z.object({
      name,
      timeLimit: z.number().int().min(30).max(3600).default(300),
      difficulty: difficultySchema.nullable().default(null),
      rounds: z.number().int().min(1).max(10).default(1),
    })).mutation(({ input }) => store.create(input)),
    joinRoom: t.procedure.input(roomInput.extend({ name })).mutation(({ input }) => store.join(input.roomId, input.name)),
    room: t.procedure.input(roomInput).query(({ input, ctx }) => store.snapshot(input.roomId, ctx.token)),
    roomUpdates: t.procedure.input(roomInput.extend({ token: z.string().length(48) })).subscription(async function* ({ input, signal }) {
      let dirty = true;
      let wake: (() => void) | undefined;
      const notify = () => { dirty = true; wake?.(); };
      const stop = store.listen(input.roomId, input.token, notify);
      signal?.addEventListener('abort', notify);
      try {
        while (!signal?.aborted) {
          if (!dirty) await new Promise<void>((resolve) => { wake = resolve; });
          if (signal?.aborted) return;
          dirty = false;
          wake = undefined;
          yield store.snapshot(input.roomId, input.token);
        }
      } finally {
        signal?.removeEventListener('abort', notify);
        stop();
      }
    }),
    start: t.procedure.input(roomInput).mutation(({ input, ctx }) => store.start(input.roomId, ctx.token)),
    restart: t.procedure.input(roomInput).mutation(({ input, ctx }) => store.restart(input.roomId, ctx.token)),
    skipBreak: t.procedure.input(roomInput).mutation(({ input, ctx }) => store.skipBreak(input.roomId, ctx.token)),
    lock: t.procedure.input(roomInput).mutation(({ input, ctx }) => store.lock(input.roomId, ctx.token)),
    resign: t.procedure.input(roomInput).mutation(({ input, ctx }) => store.resign(input.roomId, ctx.token)),
    leave: t.procedure.input(roomInput).mutation(({ input, ctx }) => store.leave(input.roomId, ctx.token)),
    submit: t.procedure.input(roomInput.extend({ code: z.string().max(50_000).refine((code) => code.trim().length > 0, 'Enter a solution before submitting.') }))
      .mutation(({ input, ctx }) => store.submit(input.roomId, ctx.token, input.code)),
    chat: t.procedure.input(roomInput.extend({ message: z.string().trim().min(1).max(200) }))
      .mutation(({ input, ctx }) => store.chat(input.roomId, ctx.token, input.message)),
    problems: t.procedure.query(() => problems.list()),
  });
}

export const problems = new ProblemRepository();
export const rooms = new RoomStore((difficulty, previousId) => problems.pick(difficulty, previousId));
export const appRouter = createRouter(rooms, problems);
export type AppRouter = typeof appRouter;
export type RouterInputs = inferRouterInputs<AppRouter>;
export type RouterOutputs = inferRouterOutputs<AppRouter>;
export type RoomSnapshot = RouterOutputs['room'];
export type Submission = RouterOutputs['submit'];
export type Session = RouterOutputs['createRoom'];

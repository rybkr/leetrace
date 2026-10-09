import { describe, expect, it } from 'vitest';
import type { Problem } from '../server/problems.js';
import { RoomStore } from '../server/rooms.js';
import type { SubmissionResult } from '../server/runner.js';

const problem: Problem = {
  id: 'sum', title: 'Sum', difficulty: 'Easy', statement: 'Return a + b.',
  entryPoint: 'Solution().sum', starterCode: 'class Solution:\n    def sum(self, a: int, b: int) -> int:\n        pass\n',
  parameters: [{ name: 'a', type: 'int' }, { name: 'b', type: 'int' }],
  returnType: 'int', tests: [{ input: [1, 2], expected: 3 }],
};
const solved: SubmissionResult = {
  passed: 1, total: 1, solved: true, charCount: 20, error: null, timeMs: 1,
  firstFailure: null, stdout: '', stderr: '',
};

function setup(rounds = 1, execute: (code: string, problem: Problem) => Promise<SubmissionResult> = async () => solved) {
  let now = 0;
  const store = new RoomStore(() => problem, execute, () => now);
  const host = store.create({ name: 'Dark', timeLimit: 30, difficulty: null, rounds });
  return { store, host, advance: (milliseconds: number) => { now += milliseconds; store.tick(); } };
}

describe('game rooms', () => {
  it('allows eight players including the host and rejects the ninth', () => {
    const { store, host } = setup();
    for (let index = 1; index < 8; index++) store.join(host.roomId, `Player ${index}`);
    expect(store.snapshot(host.roomId, host.token).players).toHaveLength(8);
    expect(() => store.join(host.roomId, 'Player 8')).toThrow('This room is full.');
    expect(store.snapshot(host.roomId, host.token).players).toHaveLength(8);
  });

  it('requires the host token and rejects duplicate display names', () => {
    const { store, host } = setup();
    const guest = store.join(host.roomId, 'Chat');
    expect(() => store.join(host.roomId, 'Dark')).toThrow('already taken');
    expect(() => store.start(host.roomId, guest.token)).toThrow('Only the host');
    expect(() => store.start(host.roomId, 'Dark')).toThrow('Rejoin');
    store.start(host.roomId, host.token);
    expect(store.snapshot(host.roomId, host.token).state).toBe('playing');
  });

  it('keeps opponent code private until a round ends', async () => {
    const { store, host } = setup();
    const guest = store.join(host.roomId, 'Chat');
    store.start(host.roomId, host.token);
    await store.submit(host.roomId, host.token, 'class Solution:\n    def sum(self, a, b):\n        return a + b');
    expect(store.snapshot(host.roomId, guest.token).rankings[0]?.code).toBeNull();
    expect(store.snapshot(host.roomId, guest.token).me.submission).toBeNull();
    store.lock(host.roomId, host.token);
    store.resign(host.roomId, guest.token);
    expect(store.snapshot(host.roomId, guest.token).rankings[0]?.code).toBe('class Solution:\n    def sum(self, a, b):\n        return a + b');
  });

  it('ranks solved submissions by length and retains the best on a regression', async () => {
    let submissionNumber = 0;
    const { store, host } = setup(1, async () => {
      submissionNumber += 1;
      return submissionNumber === 2 ? { ...solved, passed: 0, solved: false, charCount: 10 } : solved;
    });
    store.start(host.roomId, host.token);
    await store.submit(host.roomId, host.token, 'first');
    await store.submit(host.roomId, host.token, 'bad');
    const snapshot = store.snapshot(host.roomId, host.token);
    expect(snapshot.me.submission?.solved).toBe(false);
    expect(snapshot.rankings[0]?.solved).toBe(true);
    store.lock(host.roomId, host.token);
    expect(store.snapshot(host.roomId, host.token).rankings[0]?.code).toBe('first');
  });

  it('runs a 30 second break and does not let an old break skip a later round', () => {
    const { store, host, advance } = setup(3);
    store.start(host.roomId, host.token);
    advance(30_000);
    expect(store.snapshot(host.roomId, host.token).breakRemaining).toBe(30);
    store.skipBreak(host.roomId, host.token);
    advance(29_000);
    expect(store.snapshot(host.roomId, host.token).currentRound).toBe(2);
    advance(1_000);
    expect(store.snapshot(host.roomId, host.token).breakRemaining).toBe(30);
    advance(30_000);
    expect(store.snapshot(host.roomId, host.token).currentRound).toBe(3);
    advance(30_000);
    expect(store.snapshot(host.roomId, host.token).breakRemaining).toBeNull();
  });

  it('cancels a break on restart and clears submissions', async () => {
    const { store, host, advance } = setup(2);
    store.start(host.roomId, host.token);
    await store.submit(host.roomId, host.token, 'solve');
    store.lock(host.roomId, host.token);
    store.restart(host.roomId, host.token);
    advance(30_000);
    const snapshot = store.snapshot(host.roomId, host.token);
    expect(snapshot.state).toBe('lobby');
    expect(snapshot.problem).toBeNull();
    expect(snapshot.me.submission).toBeNull();
  });

  it('selects a different problem after replaying while clearing the lobby problem', () => {
    const nextProblem = { ...problem, id: 'next', title: 'Next problem' };
    const store = new RoomStore((_difficulty, previousId) => previousId === problem.id ? nextProblem : problem);
    const host = store.create({ name: 'Dark', timeLimit: 30, difficulty: 'Easy', rounds: 1 });
    store.start(host.roomId, host.token);
    expect(store.snapshot(host.roomId, host.token).problem?.id).toBe('sum');
    store.resign(host.roomId, host.token);
    store.restart(host.roomId, host.token);
    expect(store.snapshot(host.roomId, host.token).problem).toBeNull();
    store.start(host.roomId, host.token);
    const replay = store.snapshot(host.roomId, host.token);
    expect(replay.problem?.id).toBe('next');
    expect(replay.difficulty).toBe('Easy');
    expect(replay.currentRound).toBe(1);
  });

  it('rejects concurrent submissions and discards results from an old round', async () => {
    let finish: ((value: SubmissionResult) => void) | undefined;
    const execution = new Promise<SubmissionResult>((resolve) => { finish = resolve; });
    const { store, host } = setup(2, async () => execution);
    store.start(host.roomId, host.token);
    const pending = store.submit(host.roomId, host.token, 'solve');
    await expect(store.submit(host.roomId, host.token, 'solve again')).rejects.toThrow('current submission');
    store.resign(host.roomId, host.token);
    store.skipBreak(host.roomId, host.token);
    finish?.(solved);
    await expect(pending).rejects.toThrow('round ended');
    expect(store.snapshot(host.roomId, host.token).me.submission).toBeNull();
  });

  it('rejects a result if its round deadline passes before the timer tick', async () => {
    let now = 0;
    const store = new RoomStore(() => problem, async () => { now = 30_001; return solved; }, () => now);
    const host = store.create({ name: 'Dark', timeLimit: 30, difficulty: null, rounds: 1 });
    store.start(host.roomId, host.token);
    await expect(store.submit(host.roomId, host.token, 'solve')).rejects.toThrow('round ended');
    expect(store.snapshot(host.roomId, host.token).me.submission).toBeNull();
  });

  it('enforces the deadline on lock before the next timer tick', async () => {
    let now = 0;
    const store = new RoomStore(() => problem, async () => solved, () => now);
    const host = store.create({ name: 'Dark', timeLimit: 30, difficulty: null, rounds: 1 });
    store.start(host.roomId, host.token);
    await store.submit(host.roomId, host.token, 'solve');
    now = 30_001;
    expect(() => store.lock(host.roomId, host.token)).toThrow('not playing');
    expect(store.snapshot(host.roomId, host.token).state).toBe('finished');
    expect(store.snapshot(host.roomId, host.token).me.locked).toBe(false);
  });

  it('stops waiting for players whose room stream closed without leaving', () => {
    const { store, host, advance } = setup();
    const guest = store.join(host.roomId, 'Chat');
    store.listen(host.roomId, host.token, () => {});
    const closeGuest = store.listen(host.roomId, guest.token, () => {});
    store.start(host.roomId, host.token);
    closeGuest();
    store.resign(host.roomId, host.token);
    advance(14_000);
    expect(store.snapshot(host.roomId, host.token).state).toBe('playing');
    expect(store.snapshot(host.roomId, host.token).rankings.find((player) => player.name === 'Chat')?.away).toBe(false);
    advance(1_000);
    const snapshot = store.snapshot(host.roomId, host.token);
    expect(snapshot.state).toBe('finished');
    expect(snapshot.rankings.find((player) => player.name === 'Chat')?.away).toBe(true);
  });

  it('keeps waiting for a player who reconnects within the grace period', () => {
    const { store, host, advance } = setup();
    const guest = store.join(host.roomId, 'Chat');
    store.listen(host.roomId, host.token, () => {});
    const closeGuest = store.listen(host.roomId, guest.token, () => {});
    store.start(host.roomId, host.token);
    closeGuest();
    advance(5_000);
    store.listen(host.roomId, guest.token, () => {});
    store.resign(host.roomId, host.token);
    advance(20_000);
    expect(store.snapshot(host.roomId, host.token).state).toBe('playing');
  });

  it('transfers host on leave and removes empty or expired rooms', () => {
    const { store, host, advance } = setup();
    const guest = store.join(host.roomId, 'Chat');
    store.leave(host.roomId, host.token);
    expect(store.snapshot(host.roomId, guest.token).host).toBe('Chat');
    store.leave(host.roomId, guest.token);
    expect(() => store.snapshot(host.roomId, guest.token)).toThrow('not found');
    const second = store.create({ name: 'Dark', timeLimit: 30, difficulty: null, rounds: 1 });
    advance(7_200_000);
    expect(() => store.snapshot(second.roomId, second.token)).toThrow('not found');
  });
});

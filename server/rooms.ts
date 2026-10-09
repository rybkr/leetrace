import { randomBytes } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { TRPCError } from '@trpc/server';
import { publicProblem, type Difficulty, type Problem } from './problems.js';
import { runCode, type SubmissionResult } from './runner.js';

export type Submission = SubmissionResult & { submitTime: number };
interface Player {
  name: string;
  submission: Submission | null;
  best: Submission | null;
  bestCode: string | null;
  lockedAt: number | null;
  resigned: boolean;
  pending: boolean;
}
interface Room {
  id: string;
  hostToken: string;
  state: 'lobby' | 'playing' | 'finished';
  players: Map<string, Player>;
  timeLimit: number;
  difficulty: Difficulty | null;
  totalRounds: number;
  currentRound: number;
  problem: Problem | null;
  lastProblemId?: string;
  startedAt: number;
  breakUntil: number | null;
  touchedAt: number;
  epoch: number;
  messages: { id: number; sender: string; message: string }[];
  messageId: number;
  events: EventEmitter;
  presence: Map<string, { streams: number; since: number }>;
}

// Players whose room streams stay closed this long no longer hold up the round.
const AWAY_AFTER_MS = 15_000;
export interface RoomSettings {
  name: string;
  timeLimit: number;
  difficulty: Difficulty | null;
  rounds: number;
}

function reject(message: string, code: 'BAD_REQUEST' | 'NOT_FOUND' | 'UNAUTHORIZED' | 'FORBIDDEN' | 'TOO_MANY_REQUESTS' = 'BAD_REQUEST'): never {
  throw new TRPCError({ code, message });
}

function freshPlayer(name: string): Player {
  return { name, submission: null, best: null, bestCode: null, lockedAt: null, resigned: false, pending: false };
}

function better(submission: Submission, previous: Submission): boolean {
  if (submission.solved !== previous.solved) return submission.solved;
  return submission.solved ? submission.charCount < previous.charCount : submission.passed > previous.passed;
}

export class RoomStore {
  private rooms = new Map<string, Room>();

  constructor(
    private pickProblem: (difficulty: Difficulty | null, previousId?: string) => Problem,
    private execute: typeof runCode = runCode,
    private now: () => number = Date.now,
  ) {}

  create(settings: RoomSettings) {
    this.tick();
    if (this.rooms.size >= 500) reject('Room capacity reached. Try again later.', 'TOO_MANY_REQUESTS');
    let id = randomBytes(3).toString('hex').toUpperCase();
    while (this.rooms.has(id)) id = randomBytes(3).toString('hex').toUpperCase();
    const token = randomBytes(24).toString('hex');
    const room: Room = {
      id, hostToken: token, state: 'lobby', players: new Map([[token, freshPlayer(settings.name)]]),
      timeLimit: settings.timeLimit, difficulty: settings.difficulty, totalRounds: settings.rounds,
      currentRound: 0, problem: null, startedAt: 0, breakUntil: null, touchedAt: this.now(), epoch: 0,
      messages: [], messageId: 0, events: new EventEmitter(), presence: new Map(),
    };
    room.events.setMaxListeners(100);
    this.rooms.set(id, room);
    return { roomId: id, token, name: settings.name };
  }

  join(roomId: string, name: string) {
    const room = this.get(roomId);
    if (room.state !== 'lobby') reject('This game has already started.');
    if (room.players.size >= 8) reject('This room is full.');
    if ([...room.players.values()].some((player) => player.name === name)) reject('This name is already taken.');
    const token = randomBytes(24).toString('hex');
    room.players.set(token, freshPlayer(name));
    this.emit(room);
    return { roomId: room.id, token, name };
  }

  private get(roomId: string): Room {
    const room = this.rooms.get(roomId);
    if (!room) reject('Room not found.', 'NOT_FOUND');
    return room;
  }

  private authenticate(roomId: string, token: string | undefined) {
    const room = this.get(roomId);
    const player = token ? room.players.get(token) : undefined;
    if (!player) reject('Rejoin the room to continue.', 'UNAUTHORIZED');
    return { room, player };
  }

  private host(room: Room, token: string | undefined) {
    if (token !== room.hostToken) reject('Only the host can do this.', 'FORBIDDEN');
  }

  private emit(room: Room) {
    room.events.emit('update');
  }

  private away(room: Room, token: string) {
    const presence = room.presence.get(token);
    return presence !== undefined && presence.streams === 0 && this.now() - presence.since >= AWAY_AFTER_MS;
  }

  private everyoneDone(room: Room) {
    return [...room.players].every(([token, player]) => player.resigned || player.lockedAt !== null || this.away(room, token));
  }

  snapshot(roomId: string, token: string | undefined) {
    const { room, player } = this.authenticate(roomId, token);
    const rankings = [...room.players].map(([entryToken, entry]) => ({
      name: entry.name,
      solved: entry.best?.solved ?? false,
      charCount: entry.best?.charCount ?? null,
      testsPassed: entry.best?.passed ?? 0,
      testsTotal: room.problem?.tests.length ?? 0,
      lockedAt: entry.lockedAt,
      resigned: entry.resigned,
      away: this.away(room, entryToken),
      code: room.state === 'finished' ? entry.bestCode ?? null : null,
    }));
    rankings.sort((left, right) => Number(right.solved) - Number(left.solved)
      || right.testsPassed - left.testsPassed
      || (left.solved ? (left.charCount ?? Infinity) - (right.charCount ?? Infinity) : 0)
      || (left.lockedAt ?? Infinity) - (right.lockedAt ?? Infinity));
    return {
      roomId: room.id,
      host: room.players.get(room.hostToken)?.name ?? '',
      state: room.state,
      players: [...room.players.values()].map((entry) => entry.name),
      timeLimit: room.timeLimit,
      difficulty: room.difficulty,
      currentRound: room.currentRound,
      totalRounds: room.totalRounds,
      remaining: room.state === 'playing' ? Math.max(0, Math.ceil((room.startedAt + room.timeLimit * 1000 - this.now()) / 1000)) : 0,
      breakRemaining: room.breakUntil === null ? null : Math.max(0, Math.ceil((room.breakUntil - this.now()) / 1000)),
      problem: room.problem ? publicProblem(room.problem) : null,
      rankings: rankings.map((entry, index) => ({ ...entry, position: index + 1 })),
      messages: room.messages,
      me: { name: player.name, locked: player.lockedAt !== null, resigned: player.resigned, submission: player.submission },
    };
  }

  listen(roomId: string, token: string, listener: () => void) {
    const { room } = this.authenticate(roomId, token);
    if (room.events.listenerCount('update') >= 80) reject('Too many room connections.', 'TOO_MANY_REQUESTS');
    room.events.on('update', listener);
    const presence = room.presence.get(token) ?? { streams: 0, since: this.now() };
    presence.streams += 1;
    room.presence.set(token, presence);
    return () => {
      room.events.off('update', listener);
      presence.streams -= 1;
      if (presence.streams === 0) presence.since = this.now();
    };
  }

  start(roomId: string, token: string | undefined) {
    const { room } = this.authenticate(roomId, token);
    this.host(room, token);
    if (room.state !== 'lobby') reject('The game has already started.');
    this.beginRound(room);
  }

  private beginRound(room: Room) {
    const problem = this.pickProblem(room.difficulty, room.lastProblemId);
    for (const [token, player] of room.players) room.players.set(token, freshPlayer(player.name));
    room.problem = problem;
    room.lastProblemId = problem.id;
    room.state = 'playing';
    room.currentRound += 1;
    room.startedAt = this.now();
    room.touchedAt = this.now();
    room.breakUntil = null;
    room.epoch += 1;
    this.emit(room);
  }

  private finishRound(room: Room) {
    if (room.state !== 'playing') return;
    room.state = 'finished';
    room.touchedAt = this.now();
    room.breakUntil = room.currentRound < room.totalRounds ? this.now() + 30_000 : null;
    room.epoch += 1;
    this.emit(room);
  }

  private requirePlaying(room: Room) {
    if (room.state === 'playing' && this.now() >= room.startedAt + room.timeLimit * 1000) this.finishRound(room);
    if (room.state !== 'playing') reject('The round is not playing.');
  }

  skipBreak(roomId: string, token: string | undefined) {
    const { room } = this.authenticate(roomId, token);
    this.host(room, token);
    if (room.breakUntil === null) reject('There is no break to skip.');
    this.beginRound(room);
  }

  restart(roomId: string, token: string | undefined) {
    const { room } = this.authenticate(roomId, token);
    this.host(room, token);
    if (room.state !== 'finished') reject('Finish the round before restarting.');
    room.state = 'lobby';
    room.problem = null;
    room.currentRound = 0;
    room.breakUntil = null;
    room.epoch += 1;
    room.touchedAt = this.now();
    for (const [playerToken, player] of room.players) room.players.set(playerToken, freshPlayer(player.name));
    this.emit(room);
  }

  async submit(roomId: string, token: string | undefined, code: string): Promise<Submission> {
    const { room, player } = this.authenticate(roomId, token);
    if (room.state !== 'playing' || !room.problem || this.now() >= room.startedAt + room.timeLimit * 1000) reject('This round has ended.');
    if (player.lockedAt !== null || player.resigned) reject('You have already finished this round.');
    if (player.pending) reject('Wait for your current submission to finish.', 'TOO_MANY_REQUESTS');
    player.pending = true;
    const epoch = room.epoch;
    const submitTime = (this.now() - room.startedAt) / 1000;
    try {
      const result = await this.execute(code, room.problem);
      if (room.epoch !== epoch || room.players.get(token ?? '') !== player || room.state !== 'playing'
        || this.now() >= room.startedAt + room.timeLimit * 1000 || player.resigned || player.lockedAt !== null) {
        reject('This round ended while your submission was running.');
      }
      const submission = { ...result, submitTime };
      player.submission = submission;
      if (!player.best || better(submission, player.best)) {
        player.best = submission;
        player.bestCode = code;
      }
      this.emit(room);
      return submission;
    } finally {
      player.pending = false;
    }
  }

  lock(roomId: string, token: string | undefined) {
    const { room, player } = this.authenticate(roomId, token);
    this.requirePlaying(room);
    if (player.resigned || player.lockedAt !== null) reject('You have already finished this round.');
    if (!player.best?.solved) reject('Solve the problem before locking in.');
    player.lockedAt = (this.now() - room.startedAt) / 1000;
    this.maybeFinish(room);
  }

  resign(roomId: string, token: string | undefined) {
    const { room, player } = this.authenticate(roomId, token);
    this.requirePlaying(room);
    if (player.resigned || player.lockedAt !== null) reject('You have already finished this round.');
    player.resigned = true;
    this.maybeFinish(room);
  }

  private maybeFinish(room: Room) {
    if (this.everyoneDone(room)) this.finishRound(room);
    else this.emit(room);
  }

  chat(roomId: string, token: string | undefined, message: string) {
    const { room, player } = this.authenticate(roomId, token);
    room.messages.push({ id: ++room.messageId, sender: player.name, message });
    if (room.messages.length > 100) room.messages.shift();
    this.emit(room);
  }

  leave(roomId: string, token: string | undefined) {
    const { room } = this.authenticate(roomId, token);
    room.players.delete(token ?? '');
    room.presence.delete(token ?? '');
    if (!room.players.size) this.rooms.delete(room.id);
    else {
      if (room.hostToken === token) room.hostToken = room.players.keys().next().value ?? '';
      if (room.state === 'playing') this.maybeFinish(room);
    }
    this.emit(room);
  }

  tick() {
    const now = this.now();
    for (const room of this.rooms.values()) {
      if (room.state === 'playing') {
        if (now >= room.startedAt + room.timeLimit * 1000 || this.everyoneDone(room)) this.finishRound(room);
        else this.emit(room);
      } else if (room.breakUntil !== null) {
        if (now >= room.breakUntil) this.beginRound(room);
        else this.emit(room);
      } else if (now - room.touchedAt >= (room.state === 'finished' ? 3_600_000 : 7_200_000)) {
        this.rooms.delete(room.id);
        this.emit(room);
      }
    }
  }
}

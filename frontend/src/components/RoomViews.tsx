import { motion } from "framer-motion";
import { formatTimer, type RoomState } from "../hooks/useRoom";
import { ResultsTable } from "./Standings";
import { Avatar, DifficultyPill, Icon, Spinner, cx } from "./ui";

const enter = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] },
} as const;

export function Lobby({ r }: { r: RoomState }) {
  const room = r.room!;
  const players =
    room.players.length > 5
      ? [...room.players.slice(0, 3), null, ...room.players.slice(-2)]
      : room.players;
  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
      <div aria-hidden className="backdrop-grid pointer-events-none absolute inset-0" />
      <motion.div {...enter} className="relative mx-auto my-auto w-full max-w-[540px] flex-none px-4 py-10 sm:py-14">
        <div className="mb-6 text-center">
          <h1 className="text-[24px] font-semibold tracking-[-0.02em] text-fg">
            Room Code
          </h1>
        </div>

        <section className="pop overflow-hidden">
          <div className="flex flex-col items-center gap-4 px-6 py-6">
            <button
              type="button"
              onClick={() => {
                void r.copyRoom();
              }}
              aria-label={r.copied ? `Room code ${r.roomId} copied` : `Copy room code ${r.roomId}`}
              title={r.copied ? "Copied" : "Copy room code"}
              className="group flex gap-1.5 rounded-xl p-1 transition-colors"
            >
              {Array.from(r.roomId).map((char, index) => (
                <span
                  key={index}
                  className={cx(
                    "flex h-14 w-9 items-center justify-center rounded-lg border bg-sunken font-mono text-[26px] font-medium shadow-[inset_0_1px_0_rgb(255_255_255/0.03)] transition-colors sm:w-11",
                    r.copied ? "border-ok/50 text-ok" : "border-line-strong text-fg group-hover:border-line-hover",
                  )}
                >
                  {char}
                </span>
              ))}
            </button>
          </div>

          <dl className="grid grid-cols-3 border-y border-line bg-surface/60 text-center">
            <div className="flex flex-col items-center gap-1 px-3 py-3.5 sm:px-5">
              <dt className="section-label">Difficulty</dt>
              <dd>
                <DifficultyPill difficulty={room.difficulty} />
              </dd>
            </div>
            <div className="flex flex-col items-center gap-1 border-x border-line px-3 py-3.5 sm:px-5">
              <dt className="section-label">Time per round</dt>
              <dd className="font-mono text-[14px] tabular-nums text-fg">
                {formatTimer(room.timeLimit)}
              </dd>
            </div>
            <div className="flex flex-col items-center gap-1 px-3 py-3.5 sm:px-5">
              <dt className="section-label">Rounds</dt>
              <dd className="font-mono text-[14px] tabular-nums text-fg">
                {room.totalRounds}
              </dd>
            </div>
          </dl>

          <div className="flex flex-col items-center px-5 py-4">
            <h2 className="section-label mb-3 flex items-center gap-1.5">
              Players
              <span className="font-mono tabular-nums text-fg-muted">
                {room.players.length}
              </span>
            </h2>
            <ul aria-label="Players" className="flex items-center justify-center gap-3">
              {players.map((name) => name === null ? (
                <li
                  key="more"
                  aria-label={`${room.players.length - 5} more players`}
                  title={room.players.slice(3, -2).join(", ")}
                  className="text-[20px] text-fg-subtle"
                >
                  …
                </li>
              ) : (
                <motion.li
                  key={`player:${name}`}
                  layout
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  aria-label={name}
                  title={`${name}${name === room.host ? " (host)" : ""}${name === room.me.name ? " (you)" : ""}`}
                >
                  <Avatar name={name} size={32} />
                </motion.li>
              ))}
            </ul>
          </div>

          <footer className="flex items-center justify-center border-t border-line bg-surface/60 px-5 py-3.5">
            {r.isHost ? (
              <button
                type="button"
                disabled={!r.canAct}
                className="btn btn-primary"
                onClick={() => {
                  void r.start();
                }}
              >
                {r.pending ? <Spinner /> : <Icon.play size={13} />}
                {r.pending ? "Starting…" : "Start race"}
              </button>
            ) : (
              <p className="flex items-center gap-2.5 text-[13px] text-fg-muted">
                <Spinner className="text-fg-subtle" />
                <span>
                  Waiting for <span className="font-medium text-fg">{room.host}</span> to
                  start the race
                </span>
              </p>
            )}
          </footer>
        </section>
      </motion.div>
    </div>
  );
}

export function Finished({ r }: { r: RoomState }) {
  const room = r.room!;
  const breakLeft = room.breakRemaining;
  const leader = room.rankings[0];
  const lastRound = breakLeft === null;

  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
      <motion.div {...enter} className="mx-auto w-full max-w-[780px] px-4 py-10 sm:py-14">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="section-label mb-2">
              Round {room.currentRound} of {room.totalRounds}
            </p>
            <h1 className="text-[24px] font-semibold tracking-[-0.02em] text-fg">
              {lastRound ? "Race complete" : `Round ${room.currentRound} complete`}
            </h1>
            <p className="mt-1.5 text-[14px] text-fg-muted">
              {leader?.solved ? (
                <>
                  <span className="font-medium text-fg">
                    {leader.name === room.me.name ? "You" : leader.name}
                  </span>{" "}
                  took the round with{" "}
                  <span className="font-mono tabular-nums text-ok">{leader.charCount}</span>{" "}
                  characters.
                </>
              ) : (
                "Nobody solved this one."
              )}
            </p>
          </div>
          {!lastRound && (
            <div className="panel flex items-center gap-3 px-4 py-2.5">
              <Icon.clock size={15} className="text-fg-subtle" />
              <div>
                <p className="text-[11.5px] text-fg-subtle">Next round in</p>
                <p
                  className="font-mono text-[20px] font-medium leading-tight tabular-nums text-fg"
                  aria-live="off"
                >
                  {formatTimer(breakLeft ?? 0)}
                </p>
              </div>
            </div>
          )}
        </div>

        <section className="panel overflow-hidden">
          {room.problem && (
            <header className="flex items-center gap-2 border-b border-line px-4 py-3">
              <Icon.doc size={14} className="text-fg-subtle" />
              <h2 className="truncate text-[13px] font-medium text-fg">{room.problem.title}</h2>
              <DifficultyPill difficulty={room.problem.difficulty} />
            </header>
          )}
          <ResultsTable room={room} ownCode={r.code} onReview={(name, code) => r.setReview({ name, code })} />
        </section>

        <div className="mt-6 flex flex-wrap items-center justify-end gap-2">
          {!r.isHost && (
            <p className="mr-auto text-[12.5px] text-fg-subtle">
              {lastRound
                ? `Waiting for ${room.host} to start a new race.`
                : `${room.host} can skip the break.`}
            </p>
          )}
          {r.isHost && (
            <button
              type="button"
              disabled={!r.canAct}
              className="btn btn-primary"
              onClick={() => {
                void r.advance();
              }}
            >
              {r.pending ? <Spinner /> : <Icon.play size={13} />}
              {lastRound ? "Play again" : "Continue"}
            </button>
          )}
        </div>
      </motion.div>
    </div>
  );
}

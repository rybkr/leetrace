import type { ReactNode } from "react";
import type { Submission } from "../api";
import type { Ranking } from "../hooks/useRoom";
import { Icon, Kbd, Spinner, cx, shortcuts } from "./ui";

const WRONG_ANSWER = "Wrong answer.";

function Block({
  label,
  tone,
  children,
}: {
  label: string;
  tone?: "ok" | "bad";
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-[72px_minmax(0,1fr)] items-baseline gap-3 py-1.5">
      <span className="text-[12px] text-fg-subtle">{label}</span>
      <pre
        className={cx(
          "max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed [overflow-wrap:anywhere]",
          tone === "ok" ? "text-ok" : tone === "bad" ? "text-bad" : "text-[#d4d4d8]",
        )}
      >
        {children}
      </pre>
    </div>
  );
}

function Output({ label, text, tone }: { label: string; text: string; tone?: "bad" }) {
  return (
    <section>
      <h3 className="section-label mb-1.5 flex items-center gap-1.5">
        <Icon.terminal size={13} />
        {label}
      </h3>
      <pre
        className={cx(
          "max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-sunken px-3 py-2 font-mono text-[12.5px] leading-relaxed [overflow-wrap:anywhere]",
          tone === "bad" ? "text-bad/90" : "text-fg-muted",
        )}
      >
        {text}
      </pre>
    </section>
  );
}

function TestBar({ passed, total }: { passed: number; total: number }) {
  if (!total) return null;
  const segments = Math.min(total, 40);
  const filled = Math.round((passed / total) * segments);
  return (
    <div className="flex h-1.5 gap-[2px]" aria-hidden>
      {Array.from({ length: segments }, (_, index) => (
        <span
          key={index}
          className={cx(
            "flex-1 rounded-[1px]",
            index < filled ? (passed === total ? "bg-ok" : "bg-warn") : "bg-white/[0.07]",
          )}
        />
      ))}
    </div>
  );
}

export function ResultsPane({
  result,
  pending,
  review,
}: {
  result: Submission | null;
  pending: boolean;
  review: { name: string; row: Ranking | undefined } | null;
}) {
  const wrongAnswer = result?.error === WRONG_ANSWER;
  let status: ReactNode = null;
  if (review) status = <span className="pill">Read-only</span>;
  else if (pending) status = <span className="pill pill-accent"><Spinner className="!size-2.5" /> Running</span>;
  else if (result)
    status = result.solved ? (
      <span className="pill pill-ok">Accepted</span>
    ) : result.error && !wrongAnswer ? (
      <span className="pill pill-bad">Error</span>
    ) : (
      <span className="pill pill-warn">Wrong answer</span>
    );

  return (
    <>
      <header className="flex h-10 flex-none items-center gap-2 border-b border-line px-3">
        <Icon.terminal size={14} className="text-fg-subtle" />
        <h2 className="text-[13px] font-medium text-fg">
          {review ? "Submission" : "Results"}
        </h2>
        {status}
        {result && !review && (
          <span className="ml-auto inline-flex items-center gap-1 font-mono text-[12px] tabular-nums text-fg-subtle">
            <Icon.clock size={13} />
            {Math.round(result.timeMs)} ms
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {review ? (
          <ReviewSummary name={review.name} row={review.row} />
        ) : !result ? (
          <div className="flex h-full min-h-16 flex-col items-center justify-center gap-1.5 text-center">
            {pending ? (
              <p className="flex items-center gap-2 text-[13px] text-fg-muted">
                <Spinner /> Running your solution against the tests…
              </p>
            ) : (
              <>
                <p className="text-[13px] text-fg-muted">No submissions yet</p>
                <p className="flex items-center gap-1.5 text-[12px] text-fg-subtle">
                  Press <Kbd keys={shortcuts.submit} /> to run every test case.
                </p>
              </>
            )}
          </div>
        ) : (
          <div className={cx("flex flex-col gap-4 transition-opacity", pending && "opacity-50")}>
            <div className="flex flex-col gap-2.5">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                {result.solved ? (
                  <p className="flex items-center gap-2 text-[15px] font-medium text-ok">
                    <Icon.circleCheck size={17} className="self-center" />
                    All tests passed
                  </p>
                ) : (
                  <p className="flex items-center gap-2 text-[15px] font-medium text-fg">
                    <Icon.circleX size={17} className="self-center text-bad" />
                    <span className="font-mono tabular-nums">
                      {result.passed}
                      <span className="text-fg-subtle"> / {result.total}</span>
                    </span>
                    <span className="text-fg-muted">tests passed</span>
                  </p>
                )}
                {result.solved && (
                  <span className="font-mono text-[13px] tabular-nums text-fg-muted">
                    {result.charCount} chars
                  </span>
                )}
              </div>
              <TestBar passed={result.passed} total={result.total} />
            </div>

            {result.error && !wrongAnswer && (
              <pre className="overflow-auto whitespace-pre-wrap rounded-lg border border-bad/20 bg-bad/[0.06] px-3 py-2 font-mono text-[12.5px] leading-relaxed text-bad [overflow-wrap:anywhere]">
                {result.error}
              </pre>
            )}

            {result.firstFailure && (
              <section>
                <h3 className="section-label mb-1.5">First failing test</h3>
                <div className="divide-y divide-line rounded-lg border border-line bg-sunken px-3 py-0.5">
                  <Block label="Input">{result.firstFailure.input}</Block>
                  <Block label="Expected" tone="ok">
                    {result.firstFailure.expected}
                  </Block>
                  <Block label="Received" tone="bad">
                    {result.firstFailure.actual}
                  </Block>
                </div>
              </section>
            )}

            {result.stdout && <Output label="Stdout" text={result.stdout} />}
            {result.stderr && <Output label="Stderr" text={result.stderr} tone="bad" />}
          </div>
        )}
      </div>
    </>
  );
}

function ReviewSummary({ name, row }: { name: string; row: Ranking | undefined }) {
  if (!row)
    return <p className="text-[13px] text-fg-muted">Viewing {name}&rsquo;s code.</p>;
  return (
    <dl className="grid grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-2">
      {[
        ["Player", name],
        ["Result", row.solved ? "Solved" : row.resigned ? "Resigned" : "Unsolved"],
        ["Characters", row.solved && row.charCount !== null ? String(row.charCount) : "—"],
        ["Tests", `${row.testsPassed} / ${row.testsTotal}`],
      ].map(([label, value]) => (
        <div key={label} className="rounded-lg border border-line bg-sunken px-3 py-2">
          <dt className="text-[11.5px] text-fg-subtle">{label}</dt>
          <dd
            className={cx(
              "mt-0.5 truncate text-[13.5px] font-medium tabular-nums",
              label === "Result" && row.solved ? "text-ok" : "text-fg",
              (label === "Characters" || label === "Tests") && "font-mono",
            )}
          >
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

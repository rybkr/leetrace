# LeetRace

Race friends to solve the same problem in Python. Create a room, share its six-character code, and complete the Python starter in the Monaco editor. Correct solutions rank by character count, then lock time. Unsolved solutions rank by tests passed. Rooms support timed rounds, chat, resignation, and code review after each round.

## Run locally

Install Node.js 24 or newer, pnpm, and Python 3.9 or newer, then run:

```sh
pnpm install
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
pnpm dev
```

Open http://localhost:3000. Vite proxies tRPC requests to the backend on port 8000. Copy `.env.example` to `.env` to change `FRONTEND_PORT` and `BACKEND_PORT`.

For the production build:

```sh
pnpm build
pnpm start
```

The TypeScript backend serves the React build and tRPC from http://localhost:8000. Python is used only to execute submissions. The Dockerfile includes both runtimes. The Fly configuration allocates 1 GB for large suites and submission workers. `/health` returns the server's health status.

## Development

```sh
pnpm run ci      # Type check, lint, test, and build locally
pnpm typecheck
pnpm lint
pnpm test
```

One package and lockfile cover both frontend and backend.

- `frontend/src/` contains the React UI, Python editor, and tRPC client, all written in TypeScript.
- `server/` contains the tRPC router, room state, problem loader, and submission judge.
- `tests/` checks judging, room transitions, transport, and the problem corpus.
- `problems/` contains one JSON file per repaired problem. There is no separate index.

The client infers request and response types from the tRPC router. Zod validates incoming requests and problem files. [tRPC subscriptions](https://trpc.io/docs/server/subscriptions) send personalized room snapshots over server-sent events. Room membership uses a random player token, stored in session storage, so refreshing the tab restores the session and draft. Opponent code appears only after the round ends.

Rooms live in memory and reset when the server restarts. Run a single backend process per deployment.

## Problem data

The retained corpus originated from [LeetCodeDataset](https://huggingface.co/datasets/newfacade/LeetCodeDataset). Each filename is the problem ID. JSON files retain the title, difficulty, Markdown statement with examples and constraints, Python submission interface, and test inputs with expected outputs. Example explanations appear as prose outside the input and output snippets. Problems with unavailable illustrations are omitted. Floating-point problems also retain their comparison tolerances.

Tests contain data, not executable assertions. Saved reference solutions, generators, duplicate test functions, topic tags, repair logs, and older problem snapshots have been removed. Linked lists and trees are stored as JSON data and become `ListNode` and `TreeNode` objects when passed to Python solutions. The judge checks in-place mutations where required. Integers beyond JavaScript's safe range use a decimal `$bigint` tag in storage and ordinary Python integers in submissions.

Large testcase values use a `$json` field containing base64-encoded zlib JSON, with a `bytes` field for the decoded size. This lossless encoding keeps large suites small on disk and lets the judge decode expected results one testcase at a time.

## Submission judging

Python submissions complete the supplied starter, usually a method on `Solution`. The judge preloads wildcard imports from `typing`, `string`, `re`, `datetime`, `collections`, `heapq`, `bisect`, `copy`, `math`, `random`, `statistics`, `itertools`, `functools`, `operator`, `io`, `sys`, `json`, `builtins`, and `sortedcontainers`, in that order. These imports do not count toward the score. The runner uses `.venv/bin/python3` when available, or `PYTHON_BIN` if set.

The TypeScript judge runs code in a separate Python process, with a fresh namespace for every testcase. Expected outputs stay in the parent process. Feedback includes tests passed, runtime, bounded stdout and stderr, and the first failing input and output. Character counts include whitespace.

The runner limits execution time, output, and concurrency, and applies a memory cap on Linux. Python resource limits do not provide filesystem or network isolation. Public deployments accepting hostile code need operating-system or container isolation around each submission.

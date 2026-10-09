import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { JsonValue, Problem } from './problems.js';
import { decodeJsonValue, isJsonValue, jsonValueBytes, jsonValueSize } from './problem-values.js';

export interface SubmissionResult {
  passed: number;
  total: number;
  solved: boolean;
  charCount: number;
  error: string | null;
  timeMs: number;
  firstFailure: { input: string; expected: string; actual: string } | null;
  stdout: string;
  stderr: string;
}

const MAX_CONCURRENT = 4;
const MIB = 1024 * 1024;
let running = 0;
const waiting: (() => void)[] = [];

function compare(actual: JsonValue, expected: JsonValue, comparison: Problem['comparison']): boolean {
  if (typeof actual === 'number' && Number.isSafeInteger(actual) && expected !== null
      && typeof expected === 'object' && !Array.isArray(expected) && typeof expected.$bigint === 'string') {
    return BigInt(actual) === BigInt(expected.$bigint);
  }
  if (typeof expected === 'number' && Number.isSafeInteger(expected) && actual !== null
      && typeof actual === 'object' && !Array.isArray(actual) && typeof actual.$bigint === 'string') {
    return BigInt(expected) === BigInt(actual.$bigint);
  }
  if (typeof actual === 'number' && typeof expected === 'number') {
    return Number.isFinite(actual) && Number.isFinite(expected) && Math.abs(actual - expected) <= Math.max(
      comparison?.absoluteTolerance ?? 0,
      (comparison?.relativeTolerance ?? 0) * Math.max(Math.abs(actual), Math.abs(expected)),
    );
  }
  if (Array.isArray(actual) && Array.isArray(expected)) {
    if (actual.length !== expected.length) return false;
    return expected.every((value, index) => compare(actual[index]!, value, comparison));
  }
  if (actual !== null && expected !== null && typeof actual === 'object' && typeof expected === 'object'
      && !Array.isArray(actual) && !Array.isArray(expected)) {
    const keys = Object.keys(expected);
    return Object.keys(actual).length === keys.length && keys.every((key) =>
      Object.hasOwn(actual, key) && compare(actual[key]!, expected[key]!, comparison));
  }
  return actual === expected;
}

async function execute(code: string, problem: Problem): Promise<SubmissionResult> {
  const started = performance.now();
  const result: SubmissionResult = {
    passed: 0, total: problem.tests.length, solved: false,
    charCount: Array.from(code).length, error: null, timeMs: 0,
    firstFailure: null, stdout: '', stderr: '',
  };
  const worker = fileURLToPath(new URL('./runner-worker.py', import.meta.url));
  let expectedBytes = 0;
  let largestExpected = 0;
  let inputBytes = 0;
  let largestInput = 0;
  let inputs: JsonValue[][];
  let tests: Problem['tests'];
  try {
    for (const test of problem.tests) {
      const bytes = jsonValueBytes(test.expected);
      expectedBytes += bytes;
      largestExpected = Math.max(largestExpected, bytes);
      const inputSize = jsonValueBytes(test.input);
      inputBytes += inputSize;
      largestInput = Math.max(largestInput, inputSize);
    }
    const ordered = problem.tests.map((test) => {
      const input = test.input.map(decodeJsonValue);
      return { test, input, size: jsonValueSize(input) };
    }).sort((left, right) => left.size - right.size);
    tests = ordered.map(({ test }) => test);
    inputs = ordered.map(({ input }) => input);
  } catch {
    return { ...result, error: 'Invalid compressed problem input or metadata.', timeMs: Math.round(performance.now() - started) };
  }
  const caseOutputLimit = Math.min(64 * MIB, Math.max(MIB, largestExpected * 4 + 32_000));
  const totalOutputLimit = Math.min(512 * MIB, Math.max(16 * MIB, expectedBytes * 4 + problem.tests.length * 12_000));
  const submissionTimeoutMs = Math.min(60_000, Math.max(8_000,
    problem.tests.length * 20, Math.ceil(expectedBytes / (6 * MIB)) * 1_000));
  const memoryLimitMb = Math.min(512, Math.max(128,
    Math.ceil((largestExpected * 16 + (inputBytes + largestInput) * 8) / MIB) + 64));
  return new Promise((resolve) => {
    const python = process.env.PYTHON_BIN ?? (existsSync('.venv/bin/python3') ? resolvePath('.venv/bin/python3') : 'python3');
    const child = spawn(python, ['-I', '-X', 'utf8', worker], {
      cwd: tmpdir(), env: { PATH: process.env.PATH }, stdio: ['pipe', 'pipe', 'pipe'],
    });
    let pending = '';
    let received = 0;
    let outputBytes = 0;
    const stop = (message: string) => {
      result.error ??= message;
      child.kill('SIGKILL');
    };
    const timer = setTimeout(() => stop('Submission time limit exceeded.'), submissionTimeoutMs);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => {
      outputBytes += Buffer.byteLength(chunk);
      if (outputBytes > totalOutputLimit) {
        stop('Output limit exceeded.');
        return;
      }
      pending += chunk;
      let end: number;
      while ((end = pending.indexOf('\n')) !== -1) {
        const line = pending.slice(0, end);
        pending = pending.slice(end + 1);
        try {
          const record = JSON.parse(line) as { error?: string; output?: string; stdout?: string; stderr?: string };
          const test = tests[received];
          if (!test) {
            stop('Invalid runner output.');
            return;
          }
          received += 1;
          result.stdout = (result.stdout + (record.stdout ?? '')).slice(0, 5000);
          result.stderr = (result.stderr + (record.stderr ?? '')).slice(0, 5000);
          let expected: JsonValue;
          try {
            expected = decodeJsonValue(test.expected);
          } catch {
            stop('Invalid compressed expected result.');
            return;
          }
          if (record.error) {
            result.error ??= record.error;
            result.firstFailure ??= {
              input: JSON.stringify(inputs[received - 1]).slice(0, 500),
              expected: JSON.stringify(expected).slice(0, 500),
              actual: record.error.slice(0, 500),
            };
          }
          else if (typeof record.output === 'string') {
            const output: unknown = JSON.parse(record.output);
            if (!isJsonValue(output)) {
              stop('Return a finite JSON value with canonical bigint tags.');
              return;
            }
            const actual = problem.adapter === 'prefix' && typeof expected === 'number' && Array.isArray(output) ? output[0]! : output;
            if (compare(actual, expected, problem.comparison)) result.passed += 1;
            else {
              result.error ??= 'Wrong answer.';
              result.firstFailure ??= {
                input: JSON.stringify(inputs[received - 1]).slice(0, 500),
                expected: JSON.stringify(expected).slice(0, 500),
                actual: record.output.slice(0, 500),
              };
            }
          } else stop('Invalid runner output.');
        } catch {
          stop('Invalid runner output.');
        }
      }
    });
    child.stderr.on('data', (chunk: Buffer) => {
      outputBytes += chunk.length;
      if (outputBytes > totalOutputLimit) stop('Output limit exceeded.');
    });
    child.on('error', () => {
      result.error ??= 'Could not start the submission runner.';
    });
    child.on('close', (exitCode) => {
      clearTimeout(timer);
      if (exitCode !== 0 || received !== result.total) result.error ??= 'Submission exceeded its resource limits or crashed.';
      result.solved = result.passed === result.total && result.error === null;
      result.timeMs = Math.round(performance.now() - started);
      resolve(result);
    });
    child.stdin.on('error', () => { /* The child can exit before reading all input. */ });
    child.stdin.end(JSON.stringify({
      code, inputs, parameters: problem.parameters, entryPoint: problem.entryPoint,
      returnType: problem.returnType, adapter: problem.adapter,
      memoryLimitMb, cpuLimitSeconds: Math.ceil(submissionTimeoutMs / 1000), outputLimit: caseOutputLimit,
    }));
  });
}

export async function runCode(code: string, problem: Problem): Promise<SubmissionResult> {
  if (running >= MAX_CONCURRENT) await new Promise<void>((resolve) => waiting.push(resolve));
  else running += 1;
  try {
    return await execute(code, problem);
  } finally {
    const next = waiting.shift();
    if (next) next();
    else running -= 1;
  }
}

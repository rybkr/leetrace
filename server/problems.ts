import { closeSync, openSync, readSync, readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { z } from 'zod';
import { isJsonValue } from './problem-values.js';

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
const jsonValue = z.custom<JsonValue>(isJsonValue, 'Expected a finite JSON value.');
export const difficultySchema = z.enum(['Easy', 'Medium', 'Hard']);
export type Difficulty = z.infer<typeof difficultySchema>;
export const problemSchema = z.object({
  title: z.string().min(1),
  difficulty: difficultySchema,
  statement: z.string().min(1),
  starterCode: z.string().min(1),
  entryPoint: z.string().min(1),
  parameters: z.array(z.object({ name: z.string().regex(/^[A-Za-z_$][\w$]*$/), type: z.string().min(1) })),
  returnType: z.string().min(1),
  adapter: z.enum(['prefix', 'middle-list', 'reuse-list', 'mutated-tree', 'special-tree']).optional(),
  tests: z.array(z.object({ input: z.array(jsonValue), expected: jsonValue })).min(1),
  comparison: z.object({ absoluteTolerance: z.number().nonnegative().optional(), relativeTolerance: z.number().nonnegative().optional() }).optional(),
});
export type Problem = z.infer<typeof problemSchema> & { id: string };

export class ProblemRepository {
  private summaries: { id: string; title: string; difficulty: Difficulty }[] | undefined;

  constructor(private directory = resolve('problems')) {}

  list() {
    this.summaries ??= readdirSync(this.directory).filter((file) => file.endsWith('.json')).sort().map((file) => {
      const path = resolve(this.directory, file);
      const descriptor = openSync(path, 'r');
      const buffer = Buffer.alloc(2048);
      let header: string;
      try {
        header = buffer.subarray(0, readSync(descriptor, buffer)).toString('utf8');
      } finally {
        closeSync(descriptor);
      }
      // Generated headers avoid materializing every hidden test suite just to list problems.
      const match = /^\{\s*"title"\s*:\s*("(?:[^"\\]|\\.)*")\s*,\s*"difficulty"\s*:\s*("(?:[^"\\]|\\.)*")/.exec(header);
      const raw: unknown = match
        ? JSON.parse(`{"title":${match[1]},"difficulty":${match[2]}}`)
        : JSON.parse(readFileSync(path, 'utf8'));
      const metadata = problemSchema.pick({ title: true, difficulty: true }).parse(raw);
      return { ...metadata, id: file.slice(0, -5) };
    });
    return this.summaries;
  }

  pick(difficulty: Difficulty | null, previousId?: string): Problem {
    const matching = this.list().filter((problem) => !difficulty || problem.difficulty === difficulty);
    const different = matching.filter((problem) => problem.id !== previousId);
    const candidates = different.length ? different : matching;
    const problem = candidates[Math.floor(Math.random() * candidates.length)];
    if (!problem) throw new Error('No problems available for this difficulty.');
    const raw: unknown = JSON.parse(readFileSync(`${this.directory}/${problem.id}.json`, 'utf8'));
    return { ...problemSchema.parse(raw), id: problem.id };
  }
}

export function publicProblem(problem: Problem) {
  return {
    id: problem.id,
    title: problem.title,
    difficulty: problem.difficulty,
    statement: problem.statement.replace(/^\s*(?:[-*]\s*)?(?:Note|Follow-up):\s*This (?:question|problem) is the same as\s+\d+\s*:.*$/gim, '').trim(),
    starterCode: problem.starterCode,
  };
}

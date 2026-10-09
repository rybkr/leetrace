import { describe, expect, it } from 'vitest';
import { jsonValueSize } from '../server/problem-values.js';
import { publicProblem, type Problem } from '../server/problems.js';

describe('public problem statements', () => {
  it.each([
    '- Note: This question is the same as 783: https://leetcode.com/problems/minimum-distance-between-bst-nodes/',
    'Follow-up: This question is the same as 530: https://leetcode.com/problems/minimum-absolute-difference-in-bst/',
  ])('removes the equivalency note %s while preserving useful notes and constraints', (equivalency) => {
    const statement = 'Find the difference.\n\n## Constraints\n\n- 1 <= n <= 100\n- Note: Values may repeat.\n\nFollow-up: Can you solve it without extra space?';
    const problem: Problem = {
      id: 'test', title: 'Test', difficulty: 'Easy', statement: `${statement}\n\n${equivalency}`,
      starterCode: 'def solve():\n    pass', entryPoint: 'solve', parameters: [], returnType: 'int',
      tests: [{ input: [], expected: 1 }],
    };
    expect(publicProblem(problem).statement).toBe(statement);
    expect(problem.statement).toContain(equivalency);
  });
});

describe('structured input size', () => {
  it('counts scalar values, string lengths and containers without key or numeric formatting bias', () => {
    expect(jsonValueSize([10, 1_000_000])).toBe(3);
    expect(jsonValueSize([[1, 2], 'abc'])).toBe(7);
    expect(jsonValueSize({ short: 1 })).toBe(jsonValueSize({ veryLongFieldName: 1 }));
    expect(jsonValueSize({ $bigint: '9007199254740993' })).toBe(1);
  });
});

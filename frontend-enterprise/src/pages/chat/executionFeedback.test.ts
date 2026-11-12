import { describe, expect, it } from 'vitest';
import { mergeTraceLine, traceLineAllowed, traceSummary } from './chatHelpers';
import type { UIConfigRead } from '@/types';
import type { TraceLine } from './chatTypes';

describe('execution feedback', () => {
  const progress: TraceLine = {
    id: 'execution_progress', kind: 'decision', text: '正在检索邮件标题和邮件头…', state: 'running',
  };
  it('shows the current stage even when detailed traces are hidden', () => {
    const config = { show_thinking_trace: false, show_skill_trace: false, show_tool_trace: false } as UIConfigRead;
    expect(traceLineAllowed(progress, config)).toBe(true);
    expect(traceSummary({ lines: [progress], startedAt: 1 }, [progress])).toEqual({ text: progress.text, state: 'running' });
  });
  it('allows a later task to replace a stopped stage while preserving normal completed traces', () => {
    expect(mergeTraceLine({ ...progress, state: 'failed' }, progress).state).toBe('running');
    expect(mergeTraceLine({ ...progress, id: 'tool_1', state: 'completed' }, { ...progress, id: 'tool_1' }).state).toBe('completed');
  });
  it('returns to the execution record after the reply finishes', () => {
    expect(traceSummary({ lines: [progress], startedAt: 1, completedAt: 2 }, [progress])).toEqual({ text: '执行记录', state: 'completed' });
  });
});

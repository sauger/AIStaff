// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import WecomApprovalConfirmCard, {
  type WecomApprovalConfirmCardData,
} from './WecomApprovalConfirmCard';

const card: WecomApprovalConfirmCardData = {
  intent_id: 'woaint_1',
  submission_digest: 'sha256:abc',
  summary: {
    template_id: 'tpl_expense',
    applicant_mode: 'chat_partner',
    applicant_summary: '代聊天对方：张三（zhangsan）',
    creator_userid: 'zhangsan',
    creator_display_name: '张三',
    fields: [
      { key: 'reason', title: '事由', value: '差旅报销' },
      { key: 'amount', title: '金额', value: '1200' },
    ],
  },
};

afterEach(cleanup);

describe('WecomApprovalConfirmCard', () => {
  it('shows applicant then confirms with full snapshot keys', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <WecomApprovalConfirmCard card={card} onConfirm={onConfirm} onDismiss={vi.fn()} />,
    );

    expect(screen.getByText(/代聊天对方：张三/)).toBeTruthy();
    expect(screen.getByText('差旅报销')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '确认提交' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({
        intent_id: 'woaint_1',
        submission_digest: 'sha256:abc',
      }),
    );
  });

  it('dismisses without submit', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onDismiss = vi.fn();
    render(
      <WecomApprovalConfirmCard card={card} onConfirm={onConfirm} onDismiss={onDismiss} />,
    );
    await user.click(screen.getByRole('button', { name: '取消' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

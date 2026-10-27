import { Button } from '@/components/ui/button';

import {
  CHAT_DRAFT_CARD_CLASS,
  CHAT_DRAFT_FOOTER_CLASS,
  CHAT_DRAFT_HEADER_CLASS,
  CHAT_DRAFT_KICKER_CLASS,
  CHAT_DRAFT_META_GRID_CLASS,
  CHAT_DRAFT_META_ITEM_CLASS,
  CHAT_DRAFT_PROMPT_CLASS,
  CHAT_DRAFT_TITLE_CLASS,
  CHAT_DRAFT_TOP_ACTIONS_CLASS,
} from '../chatPageStyles';

import type { WecomApprovalConfirmCardData } from '../chatHelpers';

export type { WecomApprovalConfirmCardData };

type Props = {
  card: WecomApprovalConfirmCardData;
  submitted?: boolean;
  onConfirm: (card: WecomApprovalConfirmCardData) => void;
  onDismiss: () => void;
};

export default function WecomApprovalConfirmCard({
  card,
  submitted = false,
  onConfirm,
  onDismiss,
}: Props) {
  const summary = card.summary;
  return (
    <div className={CHAT_DRAFT_CARD_CLASS} data-testid="wecom-approval-confirm-card">
      <div className={CHAT_DRAFT_HEADER_CLASS}>
        <div>
          <div className={CHAT_DRAFT_KICKER_CLASS}>企微审批代发</div>
          <div className={CHAT_DRAFT_TITLE_CLASS}>请确认提交内容</div>
        </div>
        <div className={CHAT_DRAFT_TOP_ACTIONS_CLASS}>
          {!submitted && (
            <>
              <Button type="button" variant="ghost" onClick={onDismiss}>
                取消
              </Button>
              <Button type="button" onClick={() => onConfirm(card)}>
                确认提交
              </Button>
            </>
          )}
          {submitted && (
            <span className="text-[12px] text-[#1a71ff]">已提交</span>
          )}
        </div>
      </div>

      <div className={CHAT_DRAFT_META_GRID_CLASS}>
        <div className={CHAT_DRAFT_META_ITEM_CLASS}>
          <span>申请人</span>
          <strong>{summary.applicant_summary}</strong>
        </div>
        <div className={CHAT_DRAFT_META_ITEM_CLASS}>
          <span>模板</span>
          <strong>{summary.template_id}</strong>
        </div>
      </div>

      <div className={CHAT_DRAFT_PROMPT_CLASS}>
        {(summary.fields || []).map((field) => (
          <div key={field.key} className="flex gap-2 py-1 text-[13px]">
            <span className="shrink-0 text-[#757f9c]">{field.title}</span>
            <span className="min-w-0 wrap-anywhere text-[#18181a]">
              {field.value == null ? '' : String(field.value)}
            </span>
          </div>
        ))}
      </div>

      <div className={CHAT_DRAFT_FOOTER_CLASS}>
        确认后将按此不可变摘要向企微提交；修改字段需重新预览。
      </div>
    </div>
  );
}

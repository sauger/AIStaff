import { useEffect } from 'react';

import DocumentSidebar, { type OpenChatDocument } from '../components/DocumentSidebar';
import type { UseChatSession } from '../useChatSession';

/**
 * 文档浮层：绝对定位在会话区右侧，带遮罩；Escape / 点击遮罩关闭；移动端全宽。
 */
export default function DocumentOverlay({
  chat,
  document,
  onClose,
}: {
  chat: UseChatSession;
  document: OpenChatDocument;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="cw-doc-overlay" role="dialog" aria-modal="true" aria-label="文档预览">
      <button type="button" className="cw-doc-overlay-mask" aria-label="关闭文档" onClick={onClose} />
      <div className="cw-doc-overlay-body">
        <DocumentSidebar chat={chat} document={document} onClose={onClose} />
      </div>
    </div>
  );
}

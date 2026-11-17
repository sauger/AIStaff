import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import StaffdeckIcon from '@/components/StaffdeckIcon';
import { api } from '@/api/client';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '@/types';

import {
  CHAT_DOC_BODY_CLASS,
  CHAT_DOC_EDIT_ACTIONS_CLASS,
  CHAT_DOC_EDIT_CANCEL_BTN_CLASS,
  CHAT_DOC_EDIT_BOX_CLASS,
  CHAT_DOC_EDIT_ERROR_CLASS,
  CHAT_DOC_EDIT_HINT_CLASS,
  CHAT_DOC_EDIT_SUBMIT_BTN_CLASS,
  CHAT_DOC_EDIT_TEXTAREA_CLASS,
  CHAT_DOC_HEADER_CLASS,
  CHAT_DOC_LINE_CLASS,
  CHAT_DOC_LINE_EDIT_BTN_CLASS,
  CHAT_DOC_LINE_HIGHLIGHT_CLASS,
  CHAT_DOC_LINE_NUMBER_CLASS,
  CHAT_DOC_LINE_TEXT_CLASS,
  CHAT_DOC_LINE_WRAP_CLASS,
  CHAT_DOC_PDF_NOTE_CLASS,
  CHAT_DOC_SIDEBAR_CLASS,
  CHAT_DOC_STATUS_CLASS,
  CHAT_DOC_TITLE_CLASS,
  CHAT_DOC_TITLE_ICON_CLASS,
  CHAT_ICON_BUTTON_CLASS,
} from '../chatPageStyles';
import type { UseChatSession } from '../useChatSession';

export type OpenChatDocument = {
  sessionId: string;
  taskFrameId: string;
  path: string;
  displayName: string;
  /** Bumped when the same file is clicked again so the sidebar refetches. */
  revision: number;
};

type ArtifactContentResponse = {
  content: string;
  display_name?: string;
  sha256?: string;
  size?: number;
};

export type ArtifactLineEditResponse = {
  message: ChatMessage;
  line_number: number;
  new_lines: string[];
  content: string;
  sha256: string;
  size: number;
};

type LineEditOutcome = { ok: true } | { ok: false; error: string };

type DocumentSidebarProps = {
  chat: UseChatSession;
  document: OpenChatDocument;
  onClose: () => void;
};

const HIGHLIGHT_CLEAR_MS = 2400;

function isPdfDocumentPath(path: string): boolean {
  return /\.pdf$/i.test(path);
}

function artifactDownloadPath(
  document: OpenChatDocument,
  tenantId: string,
): string {
  const query = new URLSearchParams({
    tenant_id: tenantId,
    path: document.path,
  });
  return `/api/chat/sessions/${encodeURIComponent(document.sessionId)}`
    + `/artifacts/${encodeURIComponent(document.taskFrameId)}?${query.toString()}`;
}

export default function DocumentSidebar({ chat, document: openDocument, onClose }: DocumentSidebarProps) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
  const [hoveredLine, setHoveredLine] = useState<number | null>(null);
  const [focusedEditLine, setFocusedEditLine] = useState<number | null>(null);
  const [editingLine, setEditingLine] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<{ start: number; count: number } | null>(null);
  const highlightTimerRef = useRef<number | null>(null);
  const isPdf = isPdfDocumentPath(openDocument.path);

  const contentPath = useMemo(() => {
    const query = new URLSearchParams({
      tenant_id: chat.tenantId,
      path: openDocument.path,
    });
    return `/api/chat/sessions/${encodeURIComponent(openDocument.sessionId)}`
      + `/artifacts/${encodeURIComponent(openDocument.taskFrameId)}/content?${query.toString()}`;
  }, [chat.tenantId, openDocument.path, openDocument.sessionId, openDocument.taskFrameId]);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setLoadError('');
    setEditingLine(null);
    setHoveredLine(null);
    setFocusedEditLine(null);
    if (isPdf) {
      // PDF 是排版二进制：走下载端点取 blob 后内嵌预览，不提供行内编辑。
      let objectUrl = '';
      setPreviewUrl('');
      api.blob(artifactDownloadPath(openDocument, chat.tenantId))
        .then((blob) => {
          if (disposed) return;
          objectUrl = window.URL.createObjectURL(blob);
          setPreviewUrl(objectUrl);
        })
        .catch((error: unknown) => {
          if (!disposed) setLoadError(error instanceof Error ? error.message : '文档加载失败');
        })
        .finally(() => {
          if (!disposed) setLoading(false);
        });
      return () => {
        disposed = true;
        if (objectUrl) window.URL.revokeObjectURL(objectUrl);
      };
    }
    api.get<ArtifactContentResponse>(contentPath)
      .then((result) => {
        if (disposed) return;
        setContent(result.content);
      })
      .catch((error: unknown) => {
        if (disposed) return;
        setLoadError(error instanceof Error ? error.message : '文档加载失败');
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [chat.tenantId, contentPath, isPdf, openDocument, openDocument.revision]);

  useEffect(() => () => {
    if (highlightTimerRef.current !== null) window.clearTimeout(highlightTimerRef.current);
  }, []);

  const lines = useMemo(() => content.split('\n'), [content]);

  const closeEditBox = useCallback(() => {
    setEditingLine(null);
    setFocusedEditLine(null);
  }, []);

  const submitLineEdit = useCallback(
    async (lineNumber: number, originalLine: string, instruction: string): Promise<LineEditOutcome> => {
      try {
        const endpoint = `/api/chat/sessions/${encodeURIComponent(openDocument.sessionId)}`
          + `/artifacts/${encodeURIComponent(openDocument.taskFrameId)}/line-edit`;
        const result = await api.post<ArtifactLineEditResponse>(endpoint, {
          tenant_id: chat.tenantId,
          path: openDocument.path,
          line_number: lineNumber,
          original_line: originalLine,
          instruction,
        });
        setContent(result.content);
        setHighlight({ start: result.line_number, count: Math.max(1, result.new_lines.length) });
        if (highlightTimerRef.current !== null) window.clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = window.setTimeout(() => {
          highlightTimerRef.current = null;
          setHighlight(null);
        }, HIGHLIGHT_CLEAR_MS);
        chat.appendExternalAssistantMessage(openDocument.sessionId, result.message);
        setEditingLine(null);
        setFocusedEditLine(null);
        return { ok: true };
      } catch (error) {
        return {
          ok: false,
          error: error instanceof Error && error.message
            ? error.message
            : '修改失败，请稍后重试。',
        };
      }
    },
    [chat, openDocument.path, openDocument.sessionId, openDocument.taskFrameId],
  );

  const filename = useMemo(() => {
    const name = openDocument.displayName.replace(/\\/g, '/').split('/').pop()?.trim() || '';
    return name || openDocument.path;
  }, [openDocument.displayName, openDocument.path]);

  return (
    <aside className={CHAT_DOC_SIDEBAR_CLASS} aria-label={`文档侧栏 ${filename}`}>
      <header className={CHAT_DOC_HEADER_CLASS}>
        <span className={CHAT_DOC_TITLE_CLASS} data-i18n-ignore>
          <span className={CHAT_DOC_TITLE_ICON_CLASS}>
            <StaffdeckIcon name="file" size={15} />
          </span>
          <span className="min-w-0 truncate">{filename}</span>
        </span>
        <button
          type="button"
          className={CHAT_ICON_BUTTON_CLASS}
          aria-label="关闭文档侧栏"
          onClick={onClose}
        >
          <StaffdeckIcon name="close" size={16} />
        </button>
      </header>
      {isPdf ? (
        loading ? (
          <div className={CHAT_DOC_STATUS_CLASS} role="status">正在加载文档…</div>
        ) : loadError ? (
          <div className={CHAT_DOC_STATUS_CLASS} role="alert">{loadError}</div>
        ) : (
          <>
            <iframe
              title={filename}
              src={previewUrl}
              className="min-h-0 w-full flex-1 border-0"
            />
            <p className={CHAT_DOC_PDF_NOTE_CLASS}>PDF 仅支持预览；需要修改请编辑源文档（md/docx）后重新导出。</p>
          </>
        )
      ) : loading ? (
        <div className={CHAT_DOC_STATUS_CLASS} role="status">正在加载文档…</div>
      ) : loadError ? (
        <div className={CHAT_DOC_STATUS_CLASS} role="alert">{loadError}</div>
      ) : (
        <div className={CHAT_DOC_BODY_CLASS}>
          {lines.map((rawLine, index) => {
            const lineNumber = index + 1;
            const lineText = rawLine.replace(/\r$/, '');
            const isHighlighted = highlight !== null
              && lineNumber >= highlight.start
              && lineNumber < highlight.start + highlight.count;
            const isEditing = editingLine === lineNumber;
            const showEntry = hoveredLine === lineNumber || focusedEditLine === lineNumber;
            return (
              <div className={CHAT_DOC_LINE_WRAP_CLASS} key={lineNumber}>
                <div
                  className={cn(CHAT_DOC_LINE_CLASS, isHighlighted && CHAT_DOC_LINE_HIGHLIGHT_CLASS)}
                  onMouseEnter={() => setHoveredLine(lineNumber)}
                  onMouseLeave={() => setHoveredLine((current) => (current === lineNumber ? null : current))}
                >
                  <span className={CHAT_DOC_LINE_NUMBER_CLASS}>{lineNumber}</span>
                  <span className={CHAT_DOC_LINE_TEXT_CLASS} data-i18n-ignore>
                    {lineText || '\u00a0'}
                  </span>
                  {showEntry && (
                    <button
                      type="button"
                      className={CHAT_DOC_LINE_EDIT_BTN_CLASS}
                      aria-label={`修改第 ${lineNumber} 行`}
                      title={`修改第 ${lineNumber} 行`}
                      onClick={() => setEditingLine(lineNumber)}
                    >
                      <StaffdeckIcon name="edit" size={13} />
                    </button>
                  )}
                </div>
                {isEditing && (
                  <LineEditBox
                    lineNumber={lineNumber}
                    originalLine={lineText}
                    onCancel={closeEditBox}
                    onSubmit={submitLineEdit}
                    onFocusChange={(focused) => setFocusedEditLine(focused ? lineNumber : null)}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </aside>
  );
}

type LineEditBoxProps = {
  lineNumber: number;
  originalLine: string;
  onCancel: () => void;
  onSubmit: (lineNumber: number, originalLine: string, instruction: string) => Promise<LineEditOutcome>;
  onFocusChange: (focused: boolean) => void;
};

function LineEditBox({ lineNumber, originalLine, onCancel, onSubmit, onFocusChange }: LineEditBoxProps) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const submit = async () => {
    const instruction = text.trim();
    if (!instruction || submitting) return;
    setSubmitting(true);
    setError('');
    const outcome = await onSubmit(lineNumber, originalLine, instruction);
    if (!outcome.ok) {
      setError(outcome.error);
      setSubmitting(false);
      return;
    }
    setSubmitting(false);
  };

  return (
    <div className={CHAT_DOC_EDIT_BOX_CLASS}>
      <textarea
        ref={textareaRef}
        className={CHAT_DOC_EDIT_TEXTAREA_CLASS}
        aria-label={`第 ${lineNumber} 行修改要求`}
        placeholder="说明要如何修改这一行"
        value={text}
        disabled={submitting}
        onChange={(event) => setText(event.target.value)}
        onFocus={() => onFocusChange(true)}
        onBlur={() => onFocusChange(false)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === 'Escape') {
            event.preventDefault();
            onCancel();
            return;
          }
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            void submit();
          }
        }}
      />
      {error && <p className={CHAT_DOC_EDIT_ERROR_CLASS} role="alert">{error}</p>}
      <div className={CHAT_DOC_EDIT_ACTIONS_CLASS}>
        <span className={CHAT_DOC_EDIT_HINT_CLASS}>Enter 提交 · Shift+Enter 换行 · Esc 取消</span>
        <span className="flex shrink-0 gap-[6px]">
          <button
            type="button"
            className={CHAT_DOC_EDIT_CANCEL_BTN_CLASS}
            disabled={submitting}
            onClick={onCancel}
          >
            取消
          </button>
          <button
            type="button"
            className={CHAT_DOC_EDIT_SUBMIT_BTN_CLASS}
            disabled={submitting || !text.trim()}
            onClick={() => void submit()}
          >
            {submitting ? '修改中…' : '提交'}
          </button>
        </span>
      </div>
    </div>
  );
}

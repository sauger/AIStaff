// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CHAT_DOC_LINE_HIGHLIGHT_CLASS } from '../chatPageStyles';
import type { UseChatSession } from '../useChatSession';
import DocumentSidebar, { type OpenChatDocument } from './DocumentSidebar';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  blob: vi.fn(),
  appendExternalAssistantMessage: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  api: { get: mocks.get, post: mocks.post, blob: mocks.blob },
}));

const chat = {
  tenantId: 'tenant demo',
  appendExternalAssistantMessage: mocks.appendExternalAssistantMessage,
} as unknown as UseChatSession;

const document: OpenChatDocument = {
  sessionId: 'session demo',
  taskFrameId: 'task/frame',
  path: 'reports/结论说明.md',
  displayName: '结论说明.md',
  revision: 0,
};

const CONTENT = '# 标题\n\n这一行需要被改得更短一些。\n\n- 结尾\n';

const EDIT_RESPONSE = {
  message: {
    id: 'msg_edit_1',
    role: 'assistant',
    content: '已按你的要求修改《结论说明.md》第 3 行。',
    created_at: '2026-11-05T10:00:00',
    metadata: {},
  },
  line_number: 3,
  new_lines: ['改短的一行'],
  content: '# 标题\n\n改短的一行\n\n- 结尾\n',
  sha256: 'digest-after-edit',
  size: 64,
};

function lineRowByText(text: string): HTMLElement {
  const lineText = screen.getByText(text);
  const row = lineText.closest('div');
  if (!row) throw new Error(`line row not found for ${text}`);
  return row;
}

async function openEditBoxForLine(text: string) {
  const user = userEvent.setup();
  fireEvent.mouseEnter(lineRowByText(text));
  await user.click(screen.getByRole('button', { name: `修改第 ${CONTENT.split('\n').indexOf(text) + 1} 行` }));
  return screen.getByRole('textbox', { name: /修改要求/ });
}

beforeEach(() => {
  mocks.get.mockReset().mockResolvedValue({ content: CONTENT });
  mocks.post.mockReset();
  mocks.blob.mockReset();
  mocks.appendExternalAssistantMessage.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('DocumentSidebar', () => {
  it('renders the document line by line after loading', async () => {
    render(<DocumentSidebar chat={chat} document={document} onClose={vi.fn()} />);

    expect(screen.getByRole('status', { name: '' })).toBeTruthy();
    await waitFor(() => {
      expect(mocks.get).toHaveBeenCalledWith(
        '/api/chat/sessions/session%20demo/artifacts/task%2Fframe/content'
          + '?tenant_id=tenant+demo&path=reports%2F%E7%BB%93%E8%AE%BA%E8%AF%B4%E6%98%8E.md',
      );
    });
    expect(await screen.findByText('这一行需要被改得更短一些。')).toBeTruthy();
    expect(screen.getByText('结论说明.md')).toBeTruthy();
    expect(screen.getByRole('button', { name: '关闭文档侧栏' })).toBeTruthy();
  });

  it('shows the hover entry only for the hovered line and reveals the edit box in place', async () => {
    render(<DocumentSidebar chat={chat} document={document} onClose={vi.fn()} />);
    await screen.findByText('这一行需要被改得更短一些。');

    expect(screen.queryByRole('button', { name: '修改第 3 行' })).toBeNull();
    fireEvent.mouseEnter(lineRowByText('这一行需要被改得更短一些。'));
    expect(screen.getByRole('button', { name: '修改第 3 行' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '修改第 1 行' })).toBeNull();
    fireEvent.mouseLeave(lineRowByText('这一行需要被改得更短一些。'));
    expect(screen.queryByRole('button', { name: '修改第 3 行' })).toBeNull();

    fireEvent.mouseEnter(lineRowByText('这一行需要被改得更短一些。'));
    fireEvent.click(screen.getByRole('button', { name: '修改第 3 行' }));
    const box = screen.getByRole('textbox', { name: '第 3 行修改要求' });
    expect(box.getAttribute('placeholder')).toBe('说明要如何修改这一行');
  });

  it('submits the line number and original line, then swaps in the new content', async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue(EDIT_RESPONSE);
    render(<DocumentSidebar chat={chat} document={document} onClose={vi.fn()} />);
    await screen.findByText('这一行需要被改得更短一些。');

    const box = await openEditBoxForLine('这一行需要被改得更短一些。');
    await user.type(box, '把这一行改得更短');
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledWith(
        '/api/chat/sessions/session%20demo/artifacts/task%2Fframe/line-edit',
        {
          tenant_id: 'tenant demo',
          path: 'reports/结论说明.md',
          line_number: 3,
          original_line: '这一行需要被改得更短一些。',
          instruction: '把这一行改得更短',
        },
      );
    });

    await waitFor(() => {
      expect(screen.getByText('改短的一行')).toBeTruthy();
    });
    expect(screen.queryByText('这一行需要被改得更短一些。')).toBeNull();
    expect(screen.queryByRole('textbox', { name: '第 3 行修改要求' })).toBeNull();
    expect(lineRowByText('改短的一行').className).toContain(CHAT_DOC_LINE_HIGHLIGHT_CLASS);
    expect(mocks.appendExternalAssistantMessage).toHaveBeenCalledWith(
      'session demo',
      EDIT_RESPONSE.message,
    );
  });

  it('keeps the typed instruction and original content when the edit fails', async () => {
    const user = userEvent.setup();
    mocks.post.mockRejectedValue(new Error('这一行的内容已变化，请刷新文档后重试。'));
    render(<DocumentSidebar chat={chat} document={document} onClose={vi.fn()} />);
    await screen.findByText('这一行需要被改得更短一些。');

    const box = await openEditBoxForLine('这一行需要被改得更短一些。');
    await user.type(box, '把这一行改得更短');
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent)
        .toContain('这一行的内容已变化，请刷新文档后重试。');
    });
    expect((screen.getByRole('textbox', { name: '第 3 行修改要求' }) as HTMLTextAreaElement).value)
      .toBe('把这一行改得更短');
    expect(screen.getByText('这一行需要被改得更短一些。')).toBeTruthy();
  });

  it('closes the edit box on Escape without submitting', async () => {
    const user = userEvent.setup();
    render(<DocumentSidebar chat={chat} document={document} onClose={vi.fn()} />);
    await screen.findByText('这一行需要被改得更短一些。');

    const box = await openEditBoxForLine('这一行需要被改得更短一些。');
    await user.type(box, '改短');
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('textbox', { name: '第 3 行修改要求' })).toBeNull();
    expect(mocks.post).not.toHaveBeenCalled();
    expect(screen.getByText('这一行需要被改得更短一些。')).toBeTruthy();
  });

  it('inserts a newline on Shift+Enter instead of submitting', async () => {
    const user = userEvent.setup();
    render(<DocumentSidebar chat={chat} document={document} onClose={vi.fn()} />);
    await screen.findByText('这一行需要被改得更短一些。');

    const box = await openEditBoxForLine('这一行需要被改得更短一些。');
    await user.type(box, '拆成两点{Shift>}{Enter}{/Shift}第二点');

    expect((box as HTMLTextAreaElement).value).toBe('拆成两点\n第二点');
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it('refetches the document when the same file is opened again', async () => {
    const { rerender } = render(
      <DocumentSidebar chat={chat} document={document} onClose={vi.fn()} />,
    );
    await screen.findByText('这一行需要被改得更短一些。');
    expect(mocks.get).toHaveBeenCalledTimes(1);

    rerender(
      <DocumentSidebar
        chat={chat}
        document={{ ...document, revision: 1 }}
        onClose={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(mocks.get).toHaveBeenCalledTimes(2);
    });
  });
});

describe('DocumentSidebar PDF preview', () => {
  const pdfDocument: OpenChatDocument = {
    sessionId: 'session demo',
    taskFrameId: 'task/frame',
    path: 'reports/结论文档.pdf',
    displayName: '结论文档.pdf',
    revision: 0,
  };

  beforeEach(() => {
    vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:pdf-preview');
    vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => undefined);
  });

  it('renders the PDF inline via an authenticated blob without line editing', async () => {
    mocks.blob.mockResolvedValue(new Blob(['%PDF-1.4'], { type: 'application/pdf' }));
    render(<DocumentSidebar chat={chat} document={pdfDocument} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(mocks.blob).toHaveBeenCalledWith(
        '/api/chat/sessions/session%20demo/artifacts/task%2Fframe'
          + '?tenant_id=tenant+demo&path=reports%2F%E7%BB%93%E8%AE%BA%E6%96%87%E6%A1%A3.pdf',
      );
    });
    const frame = await screen.findByTitle('结论文档.pdf');
    expect(frame.getAttribute('src')).toBe('blob:pdf-preview');
    expect(screen.getByText(/PDF 仅支持预览/)).toBeTruthy();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /修改第/ })).toBeNull();
  });

  it('shows a load error when the PDF blob fails', async () => {
    mocks.blob.mockRejectedValue(new Error('Artifact not found'));
    render(<DocumentSidebar chat={chat} document={pdfDocument} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('Artifact not found');
    });
    expect(screen.queryByTitle('结论文档.pdf')).toBeNull();
  });
});

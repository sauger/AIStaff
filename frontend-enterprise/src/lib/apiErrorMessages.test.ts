import { describe, expect, it } from 'vitest';

import { apiErrorMessage } from './apiErrorMessages';

describe('apiErrorMessage', () => {
  it('localizes a known stable error code passed as a raw string', () => {
    expect(apiErrorMessage('MODEL_PROTOCOL_OPTIONS_INVALID', '保存失败')).toBe(
      '模型协议选项无效，请检查 API 协议与协议参数',
    );
    expect(apiErrorMessage('MODEL_OCR_CANNOT_BE_DEFAULT', '保存失败')).toBe(
      '扫描件 OCR 模型不能设为默认对话模型，请另建一个视觉模型',
    );
  });

  it('wraps an unknown stable error code without rewriting normal diagnostics', () => {
    expect(apiErrorMessage('SOME_NEW_BACKEND_FAILURE', '操作失败')).toBe(
      '操作失败（错误码：SOME_NEW_BACKEND_FAILURE）',
    );
    expect(apiErrorMessage('upstream request failed: timeout', '操作失败')).toBe(
      'upstream request failed: timeout',
    );
  });
});

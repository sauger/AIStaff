// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';

import { embeddingDimensionConfirmText, validateContextSettings, validateHonchoSettings } from './RuntimeSettingsPage';

const validForm = {
  show_thinking_trace: true,
  show_skill_trace: true,
  show_tool_trace: true,
  reflection_max_rounds: '1',
  agent_loop_max_actions: '32',
  context_token_budget: '32000',
  context_compaction_trigger_ratio: '0.70',
  context_recent_round_limit: '6',
  context_long_summary_token_budget: '4000',
  context_medium_summary_token_budget: '4000',
  context_allowed_roles: ['user', 'assistant'] as Array<'user' | 'assistant'>,
  context_long_summary_prefix: '历史的信息可以被总结为：',
  context_medium_summary_prefix: '近期的历史信息总结为：',
  sandbox_enabled: false,
  harness_storage_path: '',
  sandbox_network_mode: 'all' as const,
  sandbox_allowed_domains: '',
  honcho_enabled: false,
  honcho_base_url: '',
  honcho_timeout_ms: '200',
  honcho_deriver_model: '',
  honcho_deriver_base_url: '',
  honcho_deriver_api_key: '',
  honcho_embedding_model: '',
  honcho_embedding_base_url: '',
  honcho_embedding_api_key: '',
  honcho_embedding_dimensions: '1536',
};

describe('runtime context settings validation', () => {
  it('accepts the default runtime settings', () => {
    expect(validateContextSettings(validForm)).toBeNull();
  });

  it('rejects summary budgets larger than the context budget', () => {
    expect(validateContextSettings({
      ...validForm,
      context_token_budget: '7000',
    })).toBe('长期与近期摘要预算之和不能超过上下文预算');
  });

  it('requires at least one history role and both summary prefixes', () => {
    expect(validateContextSettings({
      ...validForm,
      context_allowed_roles: [],
    })).toBe('至少保留一种历史消息角色');
    expect(validateContextSettings({
      ...validForm,
      context_medium_summary_prefix: '   ',
    })).toBe('摘要前缀不能为空');
  });
});

describe('honcho runtime settings validation', () => {
  it('accepts an empty address and the default timeout', () => {
    expect(validateHonchoSettings(validForm)).toBeNull();
  });

  it('rejects a timeout outside 50–10000 and a non-http address', () => {
    expect(validateHonchoSettings({
      ...validForm,
      honcho_timeout_ms: '10',
    })).toBe('Honcho 画像读取超时必须是 50–10000 之间的整数毫秒');
    expect(validateHonchoSettings({
      ...validForm,
      honcho_timeout_ms: '200.5',
    })).toBe('Honcho 画像读取超时必须是 50–10000 之间的整数毫秒');
    expect(validateHonchoSettings({
      ...validForm,
      honcho_base_url: 'file:///tmp/honcho',
    })).toBe('Honcho 服务地址必须是 http 或 https URL');
    expect(validateHonchoSettings({
      ...validForm,
      honcho_base_url: 'http:/127.0.0.1:8000',
    })).toBe('Honcho 服务地址必须是 http 或 https URL');
    expect(validateHonchoSettings({
      ...validForm,
      honcho_base_url: 'http://user:pass@127.0.0.1:8000',
    })).toBe('Honcho 服务地址不能包含账号或密码');
  });

  it('rejects a bad model id, embedding URL, and vector dimension', () => {
    expect(validateHonchoSettings({
      ...validForm,
      honcho_deriver_model: 'deep seek',
    })).toBe('模型 ID 只能包含字母、数字和 . _ : / + -');
    expect(validateHonchoSettings({
      ...validForm,
      honcho_embedding_base_url: 'file:///tmp/embed',
    })).toBe('向量嵌入接口地址必须是 http 或 https URL');
    expect(validateHonchoSettings({
      ...validForm,
      honcho_embedding_dimensions: '0',
    })).toBe('向量维度必须是 1–8192 之间的整数');
    expect(validateHonchoSettings({
      ...validForm,
      honcho_deriver_model: 'deepseek-chat',
      honcho_embedding_model: 'text-embedding-v4',
      honcho_embedding_dimensions: '1536',
    })).toBeNull();
  });

  it('states that changing dimensions requires a vector rebuild', () => {
    expect(embeddingDimensionConfirmText(1536, 1024)).toBe(
      '向量维度将从 1536 改为 1024。已有向量数据需要重建向量库。',
    );
  });
});

import { SaveOutlined } from '../icons';
import { useEffect, useState, type ReactNode } from 'react';
import { Button as UIButton, Card, CardContent, CardHeader, CardTitle, Input, Switch, Textarea, notify } from '@/components/ui';
import { api, TENANT_ID } from '../api/client';
import type { EnterpriseAuthUser } from '../auth';
import AccountApiKeyDialog from '../components/AccountApiKeyDialog';
import type { UIConfigRead } from '../types';
import { BrainCircuit, KeyRound, Library, RotateCcw, ShieldCheck } from 'lucide-react';

type UiConfigForm = {
  show_thinking_trace: boolean;
  show_skill_trace: boolean;
  show_tool_trace: boolean;
  reflection_max_rounds: string;
  agent_loop_max_actions: string;
  context_token_budget: string;
  context_compaction_trigger_ratio: string;
  context_recent_round_limit: string;
  context_long_summary_token_budget: string;
  context_medium_summary_token_budget: string;
  context_allowed_roles: Array<'user' | 'assistant'>;
  context_long_summary_prefix: string;
  context_medium_summary_prefix: string;
  sandbox_enabled: boolean;
  harness_storage_path: string;
  sandbox_network_mode: 'all' | 'allowlist' | 'deny';
  sandbox_allowed_domains: string;
  honcho_enabled: boolean;
  honcho_base_url: string;
  honcho_timeout_ms: string;
  honcho_deriver_model: string;
  honcho_deriver_base_url: string;
  honcho_deriver_api_key: string;
  honcho_embedding_model: string;
  honcho_embedding_base_url: string;
  honcho_embedding_api_key: string;
  honcho_embedding_dimensions: string;
};

const DEFAULT_UI_CONFIG: UiConfigForm = {
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
  context_allowed_roles: ['user', 'assistant'],
  context_long_summary_prefix: '历史的信息可以被总结为：',
  context_medium_summary_prefix: '近期的历史信息总结为：',
  sandbox_enabled: false,
  harness_storage_path: '',
  sandbox_network_mode: 'all',
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

function formatDateOnly(value: string): string {
  const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? value.slice(0, 10) : date.toISOString().slice(0, 10);
}

export default function RuntimeSettingsPage({ currentUser }: { currentUser: EnterpriseAuthUser }) {
  const [form, setForm] = useState<UiConfigForm>(DEFAULT_UI_CONFIG);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState('');
  const [setupMessage, setSetupMessage] = useState('');
  const [effectiveStoragePath, setEffectiveStoragePath] = useState('');
  const [deriverKeySet, setDeriverKeySet] = useState(false);
  const [embeddingKeySet, setEmbeddingKeySet] = useState(false);
  const [sidecarApplyAvailable, setSidecarApplyAvailable] = useState(false);
  const [sidecarMessage, setSidecarMessage] = useState('');
  const [applyingSidecar, setApplyingSidecar] = useState(false);
  const [apiKeyOpen, setApiKeyOpen] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [sandboxStatus, setSandboxStatus] = useState<Pick<UIConfigRead, 'sandbox_status' | 'sandbox_status_message' | 'sandbox_status_remediation'>>({});
  const update = (patch: Partial<UiConfigForm>) => setForm((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    api.get<UIConfigRead>(`/api/enterprise/ui-config?tenant_id=${TENANT_ID}`)
      .then((row) => {
        setForm({
          show_thinking_trace: row.show_thinking_trace,
          show_skill_trace: row.show_skill_trace,
          show_tool_trace: row.show_tool_trace,
          reflection_max_rounds: String(row.reflection_max_rounds),
          agent_loop_max_actions: String(row.agent_loop_max_actions),
          context_token_budget: String(row.context_token_budget),
          context_compaction_trigger_ratio: String(row.context_compaction_trigger_ratio),
          context_recent_round_limit: String(row.context_recent_round_limit),
          context_long_summary_token_budget: String(row.context_long_summary_token_budget),
          context_medium_summary_token_budget: String(row.context_medium_summary_token_budget),
          context_allowed_roles: row.context_allowed_roles,
          context_long_summary_prefix: row.context_long_summary_prefix,
          context_medium_summary_prefix: row.context_medium_summary_prefix,
          sandbox_enabled: row.sandbox_enabled,
          harness_storage_path: row.harness_storage_path || '',
          sandbox_network_mode: row.sandbox_network_mode || 'all',
          sandbox_allowed_domains: (row.sandbox_allowed_domains || []).join('\n'),
          honcho_enabled: row.honcho_enabled === true,
          honcho_base_url: row.honcho_base_url || '',
          honcho_timeout_ms: String(row.honcho_timeout_ms ?? 200),
          honcho_deriver_model: row.honcho_deriver_model || '',
          honcho_deriver_base_url: row.honcho_deriver_base_url || '',
          honcho_deriver_api_key: '',
          honcho_embedding_model: row.honcho_embedding_model || '',
          honcho_embedding_base_url: row.honcho_embedding_base_url || '',
          honcho_embedding_api_key: '',
          honcho_embedding_dimensions: String(row.honcho_embedding_dimensions ?? 1536),
        });
        setDeriverKeySet(row.honcho_deriver_api_key_set === true);
        setEmbeddingKeySet(row.honcho_embedding_api_key_set === true);
        setSidecarApplyAvailable(row.honcho_sidecar_apply_available === true);
        setSidecarMessage('');
        setUpdatedAt(row.updated_at);
        setEffectiveStoragePath(row.effective_harness_storage_path || '');
        setSetupMessage(row.sandbox_setup_instructions || '');
        setSandboxStatus({ sandbox_status: row.sandbox_status, sandbox_status_message: row.sandbox_status_message, sandbox_status_remediation: row.sandbox_status_remediation });
        setLoadError('');
        setReady(true);
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : '运行设置加载失败';
        setLoadError(message);
        notify.error(message);
      });
  }, []);

  async function save() {
    const reflectionMaxRounds = Number(form.reflection_max_rounds);
    const agentLoopMaxActions = Number(form.agent_loop_max_actions);
    if (Number.isNaN(reflectionMaxRounds) || Number.isNaN(agentLoopMaxActions)) {
      notify.error('反思轮数与单轮最大动作数必须是数字');
      return;
    }
    const contextError = validateContextSettings(form);
    if (contextError) {
      notify.error(contextError);
      return;
    }
    const honchoError = validateHonchoSettings(form);
    if (honchoError) {
      notify.error(honchoError);
      return;
    }
    setLoading(true);
    try {
      const row = await api.put<UIConfigRead>('/api/enterprise/ui-config', {
        tenant_id: TENANT_ID,
        show_thinking_trace: form.show_thinking_trace,
        show_skill_trace: form.show_skill_trace,
        show_tool_trace: form.show_tool_trace,
        reflection_max_rounds: reflectionMaxRounds,
        agent_loop_max_actions: agentLoopMaxActions,
        context_token_budget: Number(form.context_token_budget),
        context_compaction_trigger_ratio: Number(form.context_compaction_trigger_ratio),
        context_recent_round_limit: Number(form.context_recent_round_limit),
        context_long_summary_token_budget: Number(form.context_long_summary_token_budget),
        context_medium_summary_token_budget: Number(form.context_medium_summary_token_budget),
        context_allowed_roles: form.context_allowed_roles,
        context_long_summary_prefix: form.context_long_summary_prefix.trim(),
        context_medium_summary_prefix: form.context_medium_summary_prefix.trim(),
        sandbox_enabled: form.sandbox_enabled,
        harness_storage_path: form.harness_storage_path.trim(),
        sandbox_network_mode: form.sandbox_network_mode,
        sandbox_allowed_domains: form.sandbox_allowed_domains.split(/[\n,]/).map((item) => item.trim()).filter(Boolean),
        honcho_enabled: form.honcho_enabled,
        honcho_base_url: form.honcho_base_url.trim(),
        honcho_timeout_ms: Number(form.honcho_timeout_ms),
        honcho_deriver_model: form.honcho_deriver_model.trim(),
        honcho_deriver_base_url: form.honcho_deriver_base_url.trim(),
        honcho_deriver_api_key: form.honcho_deriver_api_key,
        honcho_embedding_model: form.honcho_embedding_model.trim(),
        honcho_embedding_base_url: form.honcho_embedding_base_url.trim(),
        honcho_embedding_api_key: form.honcho_embedding_api_key,
        honcho_embedding_dimensions: Number(form.honcho_embedding_dimensions),
      });
      setUpdatedAt(row.updated_at);
      setDeriverKeySet(row.honcho_deriver_api_key_set === true);
      setEmbeddingKeySet(row.honcho_embedding_api_key_set === true);
      setSidecarApplyAvailable(row.honcho_sidecar_apply_available === true);
      setSidecarMessage(row.honcho_sidecar_apply_message || '');
      update({ honcho_deriver_api_key: '', honcho_embedding_api_key: '' });
      setEffectiveStoragePath(row.effective_harness_storage_path || '');
      if (row.restart_scheduled) {
        setRestarting(true);
        notify.success('沙盒设置已保存，RapidStaff 正在重启');
        await waitForApplicationRestart();
        window.location.reload();
        return;
      }
      notify.success('运行设置已保存');
      if (row.honcho_sidecar_apply_message) {
        if (row.honcho_sidecar_applied) notify.success(row.honcho_sidecar_apply_message);
        else notify.warning(row.honcho_sidecar_apply_message);
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存失败');
    } finally {
      setLoading(false);
    }
  }

  async function applySidecar() {
    setApplyingSidecar(true);
    try {
      const result = await api.post<{ applied: boolean; message: string }>(
        '/api/enterprise/ui-config/honcho-sidecar',
        { tenant_id: TENANT_ID },
      );
      setSidecarMessage(result.message);
      notify.success(result.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : '应用到 sidecar 失败';
      setSidecarMessage(message);
      notify.error(message);
    } finally {
      setApplyingSidecar(false);
    }
  }

  return (
    <>
      <div className="page-title">
        <div><h3>运行设置</h3><p className="text-[12px] text-muted-foreground">统一影响当前租户下所有数字员工的执行行为。</p></div>
        <UIButton disabled={!ready || loading || restarting || applyingSidecar} onClick={() => void save()}>
          {restarting ? <RotateCcw className="size-[15px] animate-spin" /> : <SaveOutlined />}
          {restarting ? '等待应用重启' : '保存设置'}
        </UIButton>
      </div>
      <Card className="editor-card settings-card">
        <CardHeader><CardTitle>执行记录与 Agent Loop</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-[16px]">
          <SwitchRow label="展示思考状态" checked={form.show_thinking_trace} onChange={(next) => update({ show_thinking_trace: next })} />
          <SwitchRow label="展示执行技能" checked={form.show_skill_trace} onChange={(next) => update({ show_skill_trace: next })} />
          <SwitchRow label="展示工具调用" checked={form.show_tool_trace} onChange={(next) => update({ show_tool_trace: next })} />
          <LabeledField label="反思轮数" hint="设为 0 时关闭反思；每轮允许模型检查当前技能和工具结果。"><Input type="number" min={0} max={5} step={1} value={form.reflection_max_rounds} onChange={(e) => update({ reflection_max_rounds: e.target.value })} /></LabeledField>
          <LabeledField label="单轮最大动作数" hint="限制一次用户输入内连续决策和工具调用的次数，避免无限循环。"><Input type="number" min={1} max={100} step={1} value={form.agent_loop_max_actions} onChange={(e) => update({ agent_loop_max_actions: e.target.value })} /></LabeledField>
        </CardContent>
      </Card>
      <Card className="editor-card settings-card overflow-hidden">
        <CardHeader className="border-b border-[#edf0f5] bg-[linear-gradient(110deg,#f8fbff_0%,#ffffff_52%,#f6f9ff_100%)]">
          <div className="flex flex-wrap items-center justify-between gap-[12px]">
            <div>
              <CardTitle className="flex items-center gap-[8px]">
                <span className="flex size-[28px] items-center justify-center rounded-[9px] bg-[#eaf2ff] text-[#1a71ff]">
                  <BrainCircuit className="size-[15px]" />
                </span>
                对话上下文与自动压缩
              </CardTitle>
              <p className="mt-[7px] text-[11px] leading-[17px] text-muted-foreground">
                租户级即时生效；单员工会话和团队成员任务共享这套参数。
              </p>
            </div>
            <UIButton
              type="button"
              variant="outline"
              className="h-[32px] gap-[6px] text-[11px]"
              onClick={() => update({
                context_token_budget: DEFAULT_UI_CONFIG.context_token_budget,
                context_compaction_trigger_ratio: DEFAULT_UI_CONFIG.context_compaction_trigger_ratio,
                context_recent_round_limit: DEFAULT_UI_CONFIG.context_recent_round_limit,
                context_long_summary_token_budget: DEFAULT_UI_CONFIG.context_long_summary_token_budget,
                context_medium_summary_token_budget: DEFAULT_UI_CONFIG.context_medium_summary_token_budget,
                context_allowed_roles: DEFAULT_UI_CONFIG.context_allowed_roles,
                context_long_summary_prefix: DEFAULT_UI_CONFIG.context_long_summary_prefix,
                context_medium_summary_prefix: DEFAULT_UI_CONFIG.context_medium_summary_prefix,
              })}
            >
              <RotateCcw className="size-[13px]" />
              恢复上下文默认值
            </UIButton>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-[18px] pt-[18px]">
          <div className="rounded-[11px] border border-[#dce8fb] bg-[#f7faff] px-[13px] py-[11px] text-[11px] leading-[18px] text-[#52637d]">
            当估算上下文达到预算 × 触发比例时，系统会把较早对话压缩为长期与近期摘要，并保留指定的最近轮次。数值越大，历史保留越完整，但模型输入成本也越高。
          </div>
          <div className="grid gap-[14px] md:grid-cols-2">
            <LabeledField label="上下文 Token 预算" hint="完整会话上下文的目标上限，范围 512–262144。">
              <Input type="number" min={512} max={262144} step={512} value={form.context_token_budget} onChange={(event) => update({ context_token_budget: event.target.value })} />
            </LabeledField>
            <LabeledField label="压缩触发比例" hint="达到预算的这个比例后开始压缩，范围 0.10–0.95。">
              <Input type="number" min={0.1} max={0.95} step={0.05} value={form.context_compaction_trigger_ratio} onChange={(event) => update({ context_compaction_trigger_ratio: event.target.value })} />
            </LabeledField>
            <LabeledField label="保留最近对话轮数" hint="压缩时不进入摘要的最近用户轮次，范围 1–50。">
              <Input type="number" min={1} max={50} step={1} value={form.context_recent_round_limit} onChange={(event) => update({ context_recent_round_limit: event.target.value })} />
            </LabeledField>
            <div className="grid grid-cols-2 gap-[10px]">
              <LabeledField label="长期摘要预算" hint="Token">
                <Input type="number" min={128} max={32768} step={128} value={form.context_long_summary_token_budget} onChange={(event) => update({ context_long_summary_token_budget: event.target.value })} />
              </LabeledField>
              <LabeledField label="近期摘要预算" hint="Token">
                <Input type="number" min={128} max={32768} step={128} value={form.context_medium_summary_token_budget} onChange={(event) => update({ context_medium_summary_token_budget: event.target.value })} />
              </LabeledField>
            </div>
          </div>
          <div className="rounded-[11px] border border-[#e6e9f0] bg-[#fbfbfc] px-[13px] py-[12px]">
            <p className="text-[12px] font-medium text-[#464c5e]">纳入历史上下文的角色</p>
            <p className="mt-[3px] text-[11px] leading-[16px] text-muted-foreground">至少保留一种角色；图片只随用户消息进入上下文。</p>
            <div className="mt-[10px] grid gap-[8px] sm:grid-cols-2">
              <SwitchRow label="用户消息" checked={form.context_allowed_roles.includes('user')} onChange={(checked) => update({ context_allowed_roles: toggleContextRole(form.context_allowed_roles, 'user', checked) })} />
              <SwitchRow label="数字员工回复" checked={form.context_allowed_roles.includes('assistant')} onChange={(checked) => update({ context_allowed_roles: toggleContextRole(form.context_allowed_roles, 'assistant', checked) })} />
            </div>
          </div>
          <div className="grid gap-[14px] md:grid-cols-2">
            <LabeledField label="长期摘要前缀" hint="注入长期摘要消息时使用，最多 200 字。">
              <Textarea rows={3} maxLength={200} value={form.context_long_summary_prefix} onChange={(event) => update({ context_long_summary_prefix: event.target.value })} />
            </LabeledField>
            <LabeledField label="近期摘要前缀" hint="注入近期摘要消息时使用，最多 200 字。">
              <Textarea rows={3} maxLength={200} value={form.context_medium_summary_prefix} onChange={(event) => update({ context_medium_summary_prefix: event.target.value })} />
            </LabeledField>
          </div>
        </CardContent>
      </Card>
      <Card className="editor-card settings-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-[8px]">
            <Library className="size-[16px]" />
            长期记忆 / Honcho
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-[16px]" aria-busy={!ready}>
          <div className="rounded-[11px] border border-[#dce8fb] bg-[#f7faff] px-[13px] py-[11px] text-[11px] leading-[18px] text-[#52637d]">
            总开关关闭后，本租户不会访问 Honcho，即使员工档案里打开了「长期用户画像」。员工也需要打开「长期用户画像」才会读写长期画像。保存后立即生效，无需改环境变量或重启服务。尚未保存过时，初始值来自 HONCHO_ENABLED、HONCHO_BASE_URL 和 HONCHO_TIMEOUT_MS。
          </div>
          {loadError && (
            <p className="text-[12px] text-[#d20b0b]">运行设置加载失败，暂不能修改 Honcho 总开关。</p>
          )}
          <SwitchRow
            label="长期记忆 / Honcho 总开关"
            checked={form.honcho_enabled}
            disabled={!ready}
            onChange={(next) => update({ honcho_enabled: next })}
          />
          <LabeledField label="Honcho 服务地址" hint="自建 Honcho 的根地址，例如 http://127.0.0.1:8000。留空则不会发起请求。">
            <Input
              value={form.honcho_base_url}
              maxLength={500}
              disabled={!ready}
              onChange={(event) => update({ honcho_base_url: event.target.value })}
              placeholder="http://127.0.0.1:8000"
            />
          </LabeledField>
          <LabeledField label="画像读取超时" hint="同步读取长期画像的超时，单位毫秒，范围 50–10000。">
            <Input
              type="number"
              min={50}
              max={10000}
              step={50}
              disabled={!ready}
              value={form.honcho_timeout_ms}
              onChange={(event) => update({ honcho_timeout_ms: event.target.value })}
            />
          </LabeledField>
          <p className="text-[11px] leading-[16px] text-muted-foreground">
            访问 Honcho 的 API 密钥（HONCHO_API_KEY）和工作区前缀（HONCHO_WORKSPACE_PREFIX）仍只从环境变量读取。下面的密钥是 Honcho 自己调用对话抽取和向量嵌入时使用的，不会改用本平台的模型密钥。
          </p>
          <div className="rounded-[11px] border border-[#f3e2c2] bg-[#fffaf3] px-[13px] py-[11px] text-[11px] leading-[18px] text-[#6d5430]">
            对话抽取模型负责从对话里抽取长期画像。向量嵌入负责把画像写成向量，不是分词。已有向量数据后不要随意更换嵌入模型或维度，否则需要重建向量库。保存后留在本平台；只有配置了 HONCHO_SIDECAR_ENV_FILE，才会写入 Honcho 的环境文件。写入后请重启 Honcho 的 api 与 deriver。写入对话抽取模型时，Honcho 的摘要、梦境和辩证问答会改用同一个对话模型和接口地址。
          </div>
          <div className="grid gap-[14px] md:grid-cols-2">
            <LabeledField label="对话抽取模型 ID" hint="OpenAI 兼容的对话模型，例如 deepseek-chat。">
              <Input
                value={form.honcho_deriver_model}
                maxLength={200}
                disabled={!ready}
                onChange={(event) => update({ honcho_deriver_model: event.target.value })}
                placeholder="deepseek-chat"
              />
            </LabeledField>
            <LabeledField label="对话抽取接口地址" hint="OpenAI 兼容接口地址，例如 https://api.deepseek.com。留空则沿用 Honcho 默认地址。">
              <Input
                value={form.honcho_deriver_base_url}
                maxLength={500}
                disabled={!ready}
                onChange={(event) => update({ honcho_deriver_base_url: event.target.value })}
                placeholder="https://api.deepseek.com"
              />
            </LabeledField>
            <LabeledField label="对话抽取 API 密钥" hint="留空则不修改已保存的密钥。明文只在本次填写时使用，保存后不再回显。">
              <Input
                type="password"
                autoComplete="new-password"
                value={form.honcho_deriver_api_key}
                maxLength={4000}
                disabled={!ready}
                onChange={(event) => update({ honcho_deriver_api_key: event.target.value })}
                placeholder={deriverKeySet ? '已配置，留空则不修改' : '尚未配置'}
              />
            </LabeledField>
            <LabeledField label="向量嵌入模型 ID" hint="例如阿里云百炼 text-embedding-v4。">
              <Input
                value={form.honcho_embedding_model}
                maxLength={200}
                disabled={!ready}
                onChange={(event) => update({ honcho_embedding_model: event.target.value })}
                placeholder="text-embedding-v4"
              />
            </LabeledField>
            <LabeledField label="向量嵌入接口地址" hint="OpenAI 兼容接口地址，例如 https://dashscope.aliyuncs.com/compatible-mode/v1。">
              <Input
                value={form.honcho_embedding_base_url}
                maxLength={500}
                disabled={!ready}
                onChange={(event) => update({ honcho_embedding_base_url: event.target.value })}
                placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
              />
            </LabeledField>
            <LabeledField label="向量嵌入 API 密钥" hint="与对话抽取密钥分开保存。留空则不修改已保存的密钥。">
              <Input
                type="password"
                autoComplete="new-password"
                value={form.honcho_embedding_api_key}
                maxLength={4000}
                disabled={!ready}
                onChange={(event) => update({ honcho_embedding_api_key: event.target.value })}
                placeholder={embeddingKeySet ? '已配置，留空则不修改' : '尚未配置'}
              />
            </LabeledField>
            <LabeledField label="向量维度" hint="默认 1536。已有向量数据后不要修改，修改后需要重建向量库。">
              <Input
                type="number"
                min={1}
                max={8192}
                step={1}
                disabled={!ready}
                value={form.honcho_embedding_dimensions}
                onChange={(event) => update({ honcho_embedding_dimensions: event.target.value })}
              />
            </LabeledField>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-[12px]">
            <p className="text-[11px] leading-[16px] text-muted-foreground">
              {sidecarApplyAvailable
                ? '当前已配置 sidecar 环境文件。保存模型设置时会写入该文件，仍需重启 Honcho 的 api 与 deriver。'
                : '当前未配置 HONCHO_SIDECAR_ENV_FILE。保存只留在本平台，「应用到 sidecar」会提示失败。'}
            </p>
            <UIButton
              type="button"
              variant="outline"
              disabled={!ready || applyingSidecar || loading}
              onClick={() => void applySidecar()}
            >
              应用到 sidecar
            </UIButton>
          </div>
          {sidecarMessage && (
            <p className="text-[12px] leading-[18px] text-[#52637d]">{sidecarMessage}</p>
          )}
        </CardContent>
      </Card>
      <Card className="editor-card settings-card">
        <CardHeader><CardTitle className="flex items-center gap-[8px]"><ShieldCheck className="size-[16px]" />执行隔离与文件存储</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-[16px]">
          <SwitchRow label="启用 SRT 沙盒" checked={form.sandbox_enabled} onChange={(next) => update({ sandbox_enabled: next })} hint="仅管理员可修改。打开或关闭后保存将自动重启 RapidStaff。默认关闭。" />
          <div className={`whitespace-pre-line rounded-md border px-[12px] py-[10px] text-[12px] leading-[18px] ${sandboxStatus.sandbox_status === 'ready' ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : sandboxStatus.sandbox_status === 'degraded' ? 'border-red-300 bg-red-50 text-red-900' : sandboxStatus.sandbox_status === 'disabled' ? 'border-slate-200 bg-slate-50 text-slate-700' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
            <div className="font-medium">沙盒状态：{sandboxStatus.sandbox_status === 'ready' ? '可用' : sandboxStatus.sandbox_status === 'degraded' ? '已降级为无沙盒（高风险）' : sandboxStatus.sandbox_status === 'disabled' ? '未启用' : '不可用'}</div>
            {sandboxStatus.sandbox_status_message && <div>{sandboxStatus.sandbox_status_message}</div>}
            {sandboxStatus.sandbox_status_remediation && <div>{sandboxStatus.sandbox_status_remediation}</div>}
          </div>
          {setupMessage && <div className="whitespace-pre-line rounded-md border border-amber-200 bg-amber-50 px-[12px] py-[10px] text-[12px] leading-[18px] text-amber-900">{setupMessage}</div>}
          {!form.sandbox_enabled && <LabeledField label="文件存储目录" hint={`沙盒关闭时，附件、任务文件与生成产物写入此目录。留空使用默认目录${effectiveStoragePath ? `：${effectiveStoragePath}` : ''}。`}><Input value={form.harness_storage_path} onChange={(e) => update({ harness_storage_path: e.target.value })} placeholder={effectiveStoragePath || '/data/staffdeck-files'} /></LabeledField>}
          {form.sandbox_enabled && <LabeledField label="网络访问" hint="统一影响所有 Harness/SRT 执行。默认联网按运行环境放行；白名单只允许列出的域名；全拒绝禁止外网。">
            <select className="h-[36px] rounded-md border border-input bg-background px-[10px] text-[13px]" value={form.sandbox_network_mode} onChange={(e) => update({ sandbox_network_mode: e.target.value as UiConfigForm['sandbox_network_mode'] })}>
              <option value="all">默认联网</option><option value="allowlist">白名单</option><option value="deny">全拒绝</option>
            </select>
          </LabeledField>}
          {form.sandbox_enabled && form.sandbox_network_mode === 'allowlist' && <LabeledField label="允许的域名" hint="每行一个域名，也支持 *.example.com。"><Textarea rows={4} value={form.sandbox_allowed_domains} onChange={(e) => update({ sandbox_allowed_domains: e.target.value })} placeholder="api.example.com\n*.internal.example.com" /></LabeledField>}
          <p className="text-[11px] leading-[16px] text-muted-foreground">关闭沙盒时，命令仍受 TaskFrame 工作区、运行时长和输出大小限制，但不再使用操作系统级 SRT 隔离。</p>
          {updatedAt && <span className="text-[12px] text-muted-foreground">最后更新：{formatDateOnly(updatedAt)}</span>}
        </CardContent>
      </Card>
      <Card className="editor-card settings-card">
        <CardHeader><CardTitle className="flex items-center gap-[8px]"><KeyRound className="size-[16px]" />API 全量密钥</CardTitle></CardHeader>
        <CardContent className="flex items-center justify-between gap-[20px]">
          <div><p className="text-[13px] font-medium text-[#2f3442]">管理员账号全量访问</p><p className="mt-[4px] text-[11px] leading-[17px] text-muted-foreground">用于 API 查询当前账号可访问的数字员工与资源。明文密钥只在创建或轮换时展示一次。</p></div>
          <UIButton variant="outline" onClick={() => setApiKeyOpen(true)}><KeyRound className="size-[15px]" />管理密钥</UIButton>
        </CardContent>
      </Card>
      <AccountApiKeyDialog account={currentUser} open={apiKeyOpen} onClose={() => setApiKeyOpen(false)} />
    </>
  );
}

export function validateContextSettings(form: UiConfigForm): string | null {
  const tokenBudget = Number(form.context_token_budget);
  const triggerRatio = Number(form.context_compaction_trigger_ratio);
  const recentRoundLimit = Number(form.context_recent_round_limit);
  const longSummaryBudget = Number(form.context_long_summary_token_budget);
  const mediumSummaryBudget = Number(form.context_medium_summary_token_budget);
  const integerValues = [tokenBudget, recentRoundLimit, longSummaryBudget, mediumSummaryBudget];
  if (![...integerValues, triggerRatio].every(Number.isFinite)) {
    return '上下文压缩参数必须是数字';
  }
  if (!integerValues.every(Number.isInteger)) {
    return 'Token 预算和保留轮数必须是整数';
  }
  if (tokenBudget < 512 || tokenBudget > 262_144) {
    return '上下文 Token 预算必须在 512–262144 之间';
  }
  if (triggerRatio < 0.1 || triggerRatio > 0.95) {
    return '压缩触发比例必须在 0.10–0.95 之间';
  }
  if (recentRoundLimit < 1 || recentRoundLimit > 50) {
    return '保留最近对话轮数必须在 1–50 之间';
  }
  if (
    longSummaryBudget < 128
    || longSummaryBudget > 32_768
    || mediumSummaryBudget < 128
    || mediumSummaryBudget > 32_768
  ) {
    return '长期与近期摘要预算必须在 128–32768 之间';
  }
  if (longSummaryBudget + mediumSummaryBudget > tokenBudget) {
    return '长期与近期摘要预算之和不能超过上下文预算';
  }
  if (form.context_allowed_roles.length === 0) {
    return '至少保留一种历史消息角色';
  }
  if (!form.context_long_summary_prefix.trim() || !form.context_medium_summary_prefix.trim()) {
    return '摘要前缀不能为空';
  }
  return null;
}

export function validateHonchoSettings(form: UiConfigForm): string | null {
  const timeout = Number(form.honcho_timeout_ms);
  if (!Number.isInteger(timeout) || timeout < 50 || timeout > 10_000) {
    return 'Honcho 画像读取超时必须是 50–10000 之间的整数毫秒';
  }
  const serviceUrlError = validateHttpUrl(form.honcho_base_url, 'Honcho 服务地址');
  if (serviceUrlError) return serviceUrlError;
  const deriverModelError = validateModelId(form.honcho_deriver_model);
  if (deriverModelError) return deriverModelError;
  const embeddingModelError = validateModelId(form.honcho_embedding_model);
  if (embeddingModelError) return embeddingModelError;
  const deriverUrlError = validateHttpUrl(form.honcho_deriver_base_url, '对话抽取接口地址');
  if (deriverUrlError) return deriverUrlError;
  const embeddingUrlError = validateHttpUrl(form.honcho_embedding_base_url, '向量嵌入接口地址');
  if (embeddingUrlError) return embeddingUrlError;
  const dimensions = Number(form.honcho_embedding_dimensions);
  if (!Number.isInteger(dimensions) || dimensions < 1 || dimensions > 8192) {
    return '向量维度必须是 1–8192 之间的整数';
  }
  return null;
}

function validateModelId(value: string): string | null {
  const text = value.trim();
  if (!text) return null;
  if (!/^[A-Za-z0-9_.:/+-]{1,200}$/.test(text)) {
    return '模型 ID 只能包含字母、数字和 . _ : / + -';
  }
  return null;
}

function validateHttpUrl(url: string, label: string): string | null {
  const value = url.trim();
  if (!value) return null;
  if (value.length > 500 || value.includes('\\')) {
    return `${label}必须是 http 或 https URL`;
  }
  const lowered = value.toLowerCase();
  const malformedScheme = lowered.startsWith('http:/') && !lowered.startsWith('http://')
    || lowered.startsWith('https:/') && !lowered.startsWith('https://');
  if (malformedScheme) {
    return `${label}必须是 http 或 https URL`;
  }
  try {
    const parsed = new URL(value);
    if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || !parsed.hostname) {
      return `${label}必须是 http 或 https URL`;
    }
    if (parsed.username || parsed.password) {
      return `${label}不能包含账号或密码`;
    }
    if (parsed.search || parsed.hash) {
      return `${label}不能包含查询参数或片段`;
    }
  } catch {
    return `${label}必须是 http 或 https URL`;
  }
  return null;
}

function toggleContextRole(
  roles: UiConfigForm['context_allowed_roles'],
  role: UiConfigForm['context_allowed_roles'][number],
  checked: boolean,
): UiConfigForm['context_allowed_roles'] {
  if (checked) return roles.includes(role) ? roles : [...roles, role];
  return roles.filter((item) => item !== role);
}

function LabeledField({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="flex flex-col gap-[6px]"><span className="text-[12px] font-medium text-[#464c5e]">{label}</span>{hint && <span className="text-[11px] leading-[16px] text-muted-foreground">{hint}</span>}{children}</label>;
}

function SwitchRow({ label, hint, checked, disabled, onChange }: { label: string; hint?: string; checked: boolean; disabled?: boolean; onChange: (next: boolean) => void }) {
  return <label className={`flex items-center justify-between gap-[16px] ${disabled ? 'opacity-60' : ''}`}><span><span className="block text-[12px] font-medium text-[#464c5e]">{label}</span>{hint && <span className="mt-[3px] block text-[11px] leading-[16px] text-muted-foreground">{hint}</span>}</span><Switch checked={checked} disabled={disabled} onCheckedChange={onChange} /></label>;
}

async function waitForApplicationRestart(): Promise<void> {
  await new Promise((resolve) => window.setTimeout(resolve, 1800));
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      await api.get('/api/health');
      return;
    } catch {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  }
  throw new Error('RapidStaff 重启超时，请稍后手动刷新页面');
}

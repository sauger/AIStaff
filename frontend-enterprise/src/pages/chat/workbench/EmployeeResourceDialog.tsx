import { useEffect, useMemo, useState } from 'react';

import { api, TENANT_ID } from '@/api/client';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from '@/components/ui/dialog';
import type {
  AgentProfileRead,
  AgentResourceBindingRead,
  AgentResourceType,
  GeneralSkillRead,
  KnowledgeBaseRead,
  SkillRead,
  ToolRead,
} from '@/types';

const RESOURCE_TYPE_LABELS: Record<AgentResourceType, string> = {
  knowledge_base: '资料',
  general_skill: '技能',
  skill: 'SOP',
  tool: '工具',
};

/** Tenant-level catalog per resource type; the dialog binds rows from here. */
const RESOURCE_LIST_PATHS: Record<AgentResourceType, string> = {
  knowledge_base: '/api/enterprise/knowledge-bases',
  general_skill: '/api/enterprise/general-skills',
  skill: '/api/enterprise/skills',
  tool: '/api/enterprise/tools',
};

type ResourceListRow = Partial<KnowledgeBaseRead & GeneralSkillRead & SkillRead & ToolRead>;

type Candidate = {
  id: string;
  name: string;
  meta: string;
};

function candidateFromRow(type: AgentResourceType, row: ResourceListRow): Candidate | null {
  const id = String(row.id || '');
  if (!id) return null;
  const name = String(
    (type === 'tool' ? row.display_name : undefined) ||
      row.name ||
      row.slug ||
      row.skill_id ||
      id,
  ).trim();
  const meta =
    type === 'tool' ? String(row.tool_type || '') : type === 'skill' ? String(row.version || '') : '';
  return { id, name: name || id, meta };
}

/**
 * 员工资料/技能/SOP/工具弹窗：勾选即绑定，保存后写回 `PUT /agents/{id}/resources`。
 * 绑定只改动当前分组，其它分组的既有绑定原样保留。样式按 Figma「全部员工-弹窗」规范。
 */
export default function EmployeeResourceDialog({
  agent,
  openType,
  onOpenChange,
  onSaved,
}: {
  agent: AgentProfileRead | null;
  openType: AgentResourceType | null;
  onOpenChange: (open: boolean) => void;
  onSaved?: (agent: AgentProfileRead, resources: AgentResourceBindingRead[]) => void;
}) {
  const type = openType;
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const focusLabel = type ? RESOURCE_TYPE_LABELS[type] : '';

  useEffect(() => {
    if (!type || !agent) return;
    setSelected(
      new Set(
        agent.resources
          .filter((item) => item.resource_type === type)
          .map((item) => item.resource_id),
      ),
    );
    setError('');
  }, [type, agent]);

  useEffect(() => {
    if (!type) return;
    let cancelled = false;
    setLoading(true);
    api
      .get<ResourceListRow[]>(`${RESOURCE_LIST_PATHS[type]}?tenant_id=${encodeURIComponent(TENANT_ID)}`)
      .then((rows) => {
        if (cancelled) return;
        setCandidates(
          rows
            .map((row) => candidateFromRow(type, row))
            .filter((row): row is Candidate => row !== null),
        );
      })
      .catch(() => {
        if (cancelled) return;
        setCandidates([]);
        setError('资源列表加载失败，请稍后重试');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [type]);

  /** Existing bindings stay listed even when the catalog no longer carries them. */
  const rows = useMemo(() => {
    if (!agent || !type) return [] as Candidate[];
    const bound = agent.resources
      .filter((item) => item.resource_type === type)
      .map((item) => ({
        id: item.resource_id,
        name: String(item.metadata?.name || item.metadata?.title || item.resource_id),
        meta: '',
      }));
    const catalogued = new Set(candidates.map((row) => row.id));
    return [...candidates, ...bound.filter((row) => !catalogued.has(row.id))];
  }, [agent, candidates, type]);

  const grouped = (Object.keys(RESOURCE_TYPE_LABELS) as AgentResourceType[]).map((key) => ({
    key,
    label: RESOURCE_TYPE_LABELS[key],
    count: agent ? agent.resources.filter((item) => item.resource_type === key).length : 0,
  }));

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function save() {
    if (!agent || !type) return;
    setSaving(true);
    setError('');
    try {
      const untouched = agent.resources
        .filter((item) => item.resource_type !== type)
        .map((item) => ({
          resource_type: item.resource_type,
          resource_id: item.resource_id,
          status: item.status === 'inactive' ? 'inactive' : 'active',
          metadata: item.metadata ?? {},
        }));
      const chosen = rows
        .filter((row) => selected.has(row.id))
        .map((row) => {
          const previous = agent.resources.find(
            (item) => item.resource_type === type && item.resource_id === row.id,
          );
          return {
            resource_type: type,
            resource_id: row.id,
            status: previous?.status === 'inactive' ? 'inactive' : 'active',
            metadata: previous?.metadata ?? { name: row.name },
          };
        });
      const updated = await api.put<AgentResourceBindingRead[]>(
        `/api/enterprise/agents/${encodeURIComponent(agent.id)}/resources`,
        { tenant_id: TENANT_ID, resources: [...untouched, ...chosen] },
      );
      onSaved?.(agent, updated);
      onOpenChange(false);
    } catch {
      setError('保存失败，请稍后重试');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={Boolean(type)} onOpenChange={onOpenChange}>
      <DialogContent
        className="cw-dialog-content"
        showCloseButton={false}
        aria-describedby={undefined}
      >
        <DialogTitle className="sr-only">{focusLabel}</DialogTitle>
        <div className="cw-dialog-header">
          <h2 className="cw-dialog-title">{focusLabel}</h2>
          <button type="button" className="cw-dialog-close" aria-label="关闭弹窗" onClick={() => onOpenChange(false)}>
            ×
          </button>
        </div>
        <div className="cw-dialog-body">
          {type && (
            <div className="cw-dialog-section">
              {loading && <p className="cw-dialog-empty">加载中…</p>}
              {!loading && rows.length === 0 && (
                <p className="cw-dialog-empty">暂无可选{focusLabel}，请先在对应页面创建。</p>
              )}
              {!loading &&
                rows.map((row) => (
                  <label key={row.id} className="cw-dialog-row">
                    <span className="cw-dialog-row-main">
                      <Checkbox
                        checked={selected.has(row.id)}
                        onCheckedChange={() => toggle(row.id)}
                        aria-label={row.name}
                      />
                      <span>{row.name}</span>
                    </span>
                    {row.meta && <span className="cw-dialog-row-meta">{row.meta}</span>}
                  </label>
                ))}
              {error && <p className="cw-dialog-empty cw-dialog-error">{error}</p>}
            </div>
          )}
          <div className="cw-dialog-section">
            <p className="cw-dialog-label">全部资源</p>
            <div className="cw-dialog-grid">
              {grouped.map((group) => (
                <div key={group.key} className="cw-dialog-grid-item">
                  <span>{group.label}</span>
                  <strong>{group.count}</strong>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="cw-dialog-footer">
          <button type="button" className="cw-dialog-cancel" onClick={() => onOpenChange(false)}>取消</button>
          <button
            type="button"
            className="cw-dialog-confirm"
            onClick={() => void save()}
            disabled={saving || loading}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

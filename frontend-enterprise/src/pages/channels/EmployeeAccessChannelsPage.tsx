import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import AppHeader from '@/components/AppHeader';
import CapabilityScopeLoading from '@/components/CapabilityScopeLoading';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import {
  ENTERPRISE_AGENT_STORAGE_KEY,
  isTeamScope,
  persistSharedAgentScope,
  readEmployeeScope,
} from '@/lib/agent-scope-storage';

import { api, TENANT_ID } from '../../api/client';
import type { EnterpriseAuthUser } from '../../auth';
import { visibleEmployeeAgents } from '../../employee';
import { EnterpriseRoute } from '../../enums/routes';
import type { AgentProfileRead, ChannelBindingRead } from '../../types';
import EmployeeAccessChannelsPanel from './EmployeeAccessChannelsPanel';

type Props = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

export default function EmployeeAccessChannelsPage({ currentUser, onLogout }: Props = {}) {
  const navigate = useNavigate();
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [agentScopeLoaded, setAgentScopeLoaded] = useState(false);

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const next = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || '';
      setAgentId(next && !isTeamScope(next) ? next : readEmployeeScope());
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await api.get<AgentProfileRead[]>(
          `/api/enterprise/agents?tenant_id=${encodeURIComponent(TENANT_ID)}`,
        );
        if (cancelled) return;
        const visible = visibleEmployeeAgents(rows, currentUser, { activeOnly: true });
        const stored = window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '';
        if (isTeamScope(agentId) || isTeamScope(stored)) {
          return;
        }
        const preferred =
          visible.find((item) => item.id === agentId && !item.is_overall)
          || visible.find((item) => !item.is_overall);
        const nextId = preferred?.id || '';
        if (nextId && nextId !== agentId) {
          persistSharedAgentScope(nextId);
          setAgentId(nextId);
        }
      } catch (error) {
        notify.error(error instanceof Error ? error.message : '加载员工失败');
      } finally {
        if (!cancelled) setAgentScopeLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, currentUser]);

  if (!agentScopeLoaded) return <CapabilityScopeLoading />;

  const openChannelsManager = () => {
    navigate(EnterpriseRoute.Channels);
  };

  const openMail = () => {
    if (agentId) {
      persistSharedAgentScope(agentId);
    }
    navigate(EnterpriseRoute.Mail);
  };

  const openBinding = (_binding: ChannelBindingRead) => {
    navigate(EnterpriseRoute.Channels);
  };

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="接入渠道"
        description="当前数字员工自己的企微 / 钉钉 / 飞书 / 微信客服 / 邮件。新建与转移请到「渠道管理」。"
      />

      {isTeamScope(agentId) || !agentId ? (
        <p className="mt-[20px] text-[13px] text-muted-foreground">
          请先选择一个数字员工，再查看接入渠道。
        </p>
      ) : (
        <div className="mt-[20px] max-w-[720px]">
          <EmployeeAccessChannelsPanel
            agentId={agentId}
            onCreateChannel={openChannelsManager}
            onOpenChannel={openBinding}
            onOpenMail={openMail}
          />
          <div className="mt-[12px]">
            <UIButton
              type="button"
              variant="outline"
              className="h-8 rounded-[10px] px-3 text-[12px]"
              onClick={openChannelsManager}
            >
              打开渠道管理
            </UIButton>
          </div>
        </div>
      )}
    </div>
  );
}

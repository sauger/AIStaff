import { useNavigate } from 'react-router-dom';

import AppHeader from '@/components/AppHeader';
import { UnderlineTabs, type UnderlineTabItem } from '@/components/ui';

import type { EnterpriseAuthUser } from '../../auth';
import { EnterpriseRoute } from '../../enums/routes';
import CompanyMembersPage from './CompanyMembersPage';
import OrgStructurePage from '../OrgStructurePage';

export type CompanyTab = 'organization' | 'members';

const TABS: UnderlineTabItem<CompanyTab>[] = [
  { value: 'organization', label: '组织架构' },
  { value: 'members', label: '成员' },
];

const ROUTES: Record<CompanyTab, string> = {
  organization: EnterpriseRoute.CompanyOrganization,
  members: EnterpriseRoute.CompanyMembers,
};

export default function CompanyPage({
  tab,
  currentUser,
  onLogout,
}: {
  tab: CompanyTab;
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}) {
  const navigate = useNavigate();

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="企业管理"
        description="维护企业信息、组织架构，以及企业人员与数字员工的成员关系。"
      />
      <UnderlineTabs
        className="mt-[20px] mb-[16px]"
        aria-label="企业管理分区"
        value={tab}
        onChange={(next) => navigate(ROUTES[next])}
        items={TABS}
      />
      {tab === 'organization' ? (
        <OrgStructurePage currentUser={currentUser} embedded />
      ) : (
        <CompanyMembersPage currentUser={currentUser} />
      )}
    </div>
  );
}

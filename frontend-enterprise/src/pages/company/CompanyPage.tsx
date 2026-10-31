import type { ReactNode } from 'react';

import AppHeader from '@/components/AppHeader';

import type { EnterpriseAuthUser } from '../../auth';
import CompanyMembersPage from './CompanyMembersPage';
import OrgStructurePage from '../OrgStructurePage';

export type CompanySection = 'organization' | 'members';

export function CompanyShell({
  currentUser,
  onLogout,
  children,
}: {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title="企业管理"
        description="维护企业信息、组织架构、企业人员与数字员工的成员关系，以及企微自建应用凭证。"
      />
      <div className="mt-[20px]">{children}</div>
    </div>
  );
}

export default function CompanyPage({
  section,
  currentUser,
  onLogout,
}: {
  section: CompanySection;
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}) {
  return (
    <CompanyShell currentUser={currentUser} onLogout={onLogout}>
      {section === 'organization' ? (
        <OrgStructurePage currentUser={currentUser} embedded />
      ) : (
        <CompanyMembersPage currentUser={currentUser} />
      )}
    </CompanyShell>
  );
}

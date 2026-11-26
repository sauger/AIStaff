import { employeeDisplayName } from '@/employee';
import type { AgentProfileRead } from '@/types';

import EmployeeIdleAvatar from './EmployeeIdleAvatar';
import type { YardEmployee } from './EmployeeYardCard';

/** 会话空态：员工吉祥物特写 + 「和{name}开始协作」。 */
export default function WorkbenchEmptyState({
  agent,
  yardEmployee,
}: {
  agent: AgentProfileRead | null;
  yardEmployee: YardEmployee | null;
}) {
  const name = agent ? employeeDisplayName(agent) : '数字员工';
  return (
    <div className="cw-conversation-empty">
      {yardEmployee && <EmployeeIdleAvatar artId={yardEmployee.artId} color={yardEmployee.color} name={name} />}
      <h2>和{name}开始协作</h2>
      <p>描述你的目标，或添加相关文档。</p>
    </div>
  );
}

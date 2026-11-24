/** 工位上要画出来的员工状态。空闲仍用站立形象，其余三种坐在同一张桌子后。 */
export type EmployeeDeskStatus = 'working' | 'offline' | 'unread' | 'idle';

/**
 * 离线优先于进行中：下线员工不再显示工作场景。
 * 在线时，正在执行盖过未读完成。
 */
export function employeeDeskStatus(input: {
  online: boolean;
  running: boolean;
  unread: boolean;
}): EmployeeDeskStatus {
  if (!input.online) return 'offline';
  if (input.running) return 'working';
  if (input.unread) return 'unread';
  return 'idle';
}

/** 下线员工排到活跃员工后面，避免占满 10 个工位。 */
export function compareDeskStatus(left: EmployeeDeskStatus, right: EmployeeDeskStatus): number {
  const rank = (status: EmployeeDeskStatus) => (status === 'offline' ? 1 : 0);
  return rank(left) - rank(right);
}

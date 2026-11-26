import { ART_ASSETS } from './employeeArt';

/** 设计稿：展开 195×42 完整 logo；折叠只保留 44×43 的猫头 mark。 */
export default function WorkbenchBrand({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <img
      className={collapsed ? 'cw-brand-mark' : 'cw-brand'}
      src={collapsed ? ART_ASSETS.brandMark : ART_ASSETS.brand}
      alt="RapidStaff"
      width={collapsed ? 44 : 195}
      height={collapsed ? 43 : 42}
      draggable={false}
    />
  );
}

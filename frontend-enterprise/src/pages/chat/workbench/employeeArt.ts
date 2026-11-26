import rawBackend from '@/assets/employee-art/backend.svg?raw';
import rawBackendAvatar from '@/assets/employee-art/backend-avatar.svg';
import rawBrand from '@/assets/employee-art/brand.svg';
import rawBrandMark from '@/assets/employee-art/brand-mark.svg';
import rawDesign from '@/assets/employee-art/design.svg?raw';
import rawDesignAvatar from '@/assets/employee-art/design-avatar.svg';
import rawEmpty from '@/assets/employee-art/empty.svg';
import rawFrontend from '@/assets/employee-art/frontend.svg?raw';
import rawFrontendAvatar from '@/assets/employee-art/frontend-avatar.svg';
import rawInk from '@/assets/employee-art/ink.svg?raw';
import rawOps from '@/assets/employee-art/ops.svg?raw';
import rawOpsAvatar from '@/assets/employee-art/ops-avatar.svg';
import rawPm from '@/assets/employee-art/pm.svg?raw';
import rawPmAvatar from '@/assets/employee-art/pm-avatar.svg';
import rawProduct from '@/assets/employee-art/product.svg?raw';
import rawProductAvatar from '@/assets/employee-art/product-avatar.svg';
import rawQa from '@/assets/employee-art/qa.svg?raw';
import rawQaAvatar from '@/assets/employee-art/qa-avatar.svg';
import rawSales from '@/assets/employee-art/sales.svg?raw';
import rawSalesAvatar from '@/assets/employee-art/sales-avatar.svg';
import rawTick from '@/assets/employee-art/tick.svg';
import rawToken from '@/assets/employee-art/token.svg?raw';
import rawTokenAvatar from '@/assets/employee-art/token-avatar.svg';
import workBody from '@/assets/employee-art/work-body.svg?raw';
import workDesk from '@/assets/employee-art/work-desk.svg';
import workHands from '@/assets/employee-art/work-hands.svg?raw';
import workLaptop from '@/assets/employee-art/work-laptop.svg?raw';
import workSweat from '@/assets/employee-art/work-sweat.svg?raw';
import completedSignUrl from '@/assets/employee-art/completed-sign.svg';

export const EMPLOYEE_ART_IDS = [
  'pm', 'product', 'design', 'token', 'backend', 'frontend', 'ops', 'qa', 'sales',
] as const;

export type EmployeeArtId = (typeof EMPLOYEE_ART_IDS)[number];

const RAW_ART: Record<EmployeeArtId, string> = {
  pm: rawPm,
  product: rawProduct,
  design: rawDesign,
  token: rawToken,
  backend: rawBackend,
  frontend: rawFrontend,
  ops: rawOps,
  qa: rawQa,
  sales: rawSales,
};

const AVATAR_ART: Record<EmployeeArtId, string> = {
  pm: rawPmAvatar,
  product: rawProductAvatar,
  design: rawDesignAvatar,
  token: rawTokenAvatar,
  backend: rawBackendAvatar,
  frontend: rawFrontendAvatar,
  ops: rawOpsAvatar,
  qa: rawQaAvatar,
  sales: rawSalesAvatar,
};

export const ART_ASSETS = {
  brand: rawBrand,
  brandMark: rawBrandMark,
  empty: rawEmpty,
  tick: rawTick,
  workDesk,
};

export const WORK_SCENE_ASSETS = { body: workBody, hands: workHands, laptop: workLaptop, sweat: workSweat };

export const INK_COLORS: Record<EmployeeArtId, string> = {
  pm: '#000000',
  product: '#6155F5',
  design: '#FF383C',
  token: '#FF8D28',
  backend: '#00C0E8',
  frontend: '#34C759',
  ops: '#8B5E3C',
  qa: '#0088FF',
  sales: '#CF2BE4',
};

/** 工位颜色池：同一屏内按员工序号取色，保证每个人颜色不同。 */
export const YARD_COLORS = [
  '#000000',
  '#6155F5',
  '#FF383C',
  '#FF8D28',
  '#00C0E8',
  '#34C759',
  '#8B5E3C',
  '#0088FF',
  '#CF2BE4',
  '#F5B400',
  '#FF6B9A',
  '#12B5A5',
] as const;

/** role_key / role_name → 原型工位插画。 */
const ROLE_MATCHERS: Array<{ art: EmployeeArtId; keys: RegExp }> = [
  { art: 'pm', keys: /project|pm|项目经理|项目/ },
  { art: 'product', keys: /product|产品/ },
  { art: 'design', keys: /design|ui|ue|设计/ },
  { art: 'token', keys: /token|运营/ },
  { art: 'backend', keys: /backend|后端/ },
  { art: 'frontend', keys: /frontend|前端|研发|开发/ },
  { art: 'ops', keys: /ops|运维|行政/ },
  { art: 'qa', keys: /qa|测试|质量/ },
  { art: 'sales', keys: /sales|销售|商务/ },
];

function hashString(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

/** 仅返回设计稿明确给出的角色颜色；未命中角色返回 null，由调用方补色。 */
export function roleColor(roleKey: string, roleName: string): string | null {
  const haystack = `${roleKey || ''} ${roleName || ''}`.toLowerCase();
  for (const matcher of ROLE_MATCHERS) {
    if (matcher.keys.test(haystack)) return INK_COLORS[matcher.art];
  }
  return null;
}

export function artForRole(roleKey: string, roleName: string): EmployeeArtId {
  const haystack = `${roleKey || ''} ${roleName || ''}`.toLowerCase();
  for (const matcher of ROLE_MATCHERS) {
    if (matcher.keys.test(haystack)) return matcher.art;
  }
  return EMPLOYEE_ART_IDS[hashString(haystack || 'staff') % EMPLOYEE_ART_IDS.length];
}

export function isEmployeeArtId(value: string): value is EmployeeArtId {
  return (EMPLOYEE_ART_IDS as readonly string[]).includes(value);
}

/** 未知 id 哈希到九个原型插画之一。 */
export function artForUnknown(seed: string): EmployeeArtId {
  return EMPLOYEE_ART_IDS[hashString(seed) % EMPLOYEE_ART_IDS.length];
}

/** 内联多个员工 SVG 时渐变/滤镜 id 会冲突：id= 与 url(#...) 同步加后缀。 */
export function scopeSvg(raw: string, suffix: string): string {
  const cacheKey = `${suffix}::${raw.length}`;
  const cached = scopeCache.get(cacheKey);
  if (cached) return cached;
  const output = raw
    .replace(/\sid="([^"]+)"/g, (_match, id: string) => ` id="${id}-${suffix}"`)
    .replace(/url\(#([^)]+)\)/g, (_match, id: string) => `url(#${id}-${suffix})`);
  scopeCache.set(cacheKey, output);
  return output;
}

const scopeCache = new Map<string, string>();

/** 小墨团形象：替换 fill="black" 为角色色，白色眼睛包一层 .idle-gaze 供动效使用。 */
export function inkMarkup(
  artId: EmployeeArtId,
  full = true,
  working = false,
  color = INK_COLORS[artId],
): string {
  let raw = rawInk.replace(/fill="black"/g, `fill="${color}"`);
  if (working) raw = raw.replace(/<path class="ink-arm"[^>]*\/>/g, '');
  raw = raw.replace(
    /<path([^>]*fill="white"[^>]*)\/>/g,
    '<g class="idle-gaze"><g class="ink-eye"><path$1/></g></g>',
  );
  return full ? raw : raw.replace(/<svg[^>]*>/, '').replace('</svg>', '');
}

export function inkAvatarMarkup(artId: EmployeeArtId, color?: string): string {
  return inkMarkup(artId, true, false, color);
}

const portraitCache = new Map<string, string>();

/** 头像特写：取 .idle-body，viewBox 收紧到 x+55 y+5 85 90。 */
export function portraitBody(
  raw: string,
  appearance: string,
  artId: EmployeeArtId,
  color?: string,
): string {
  const key = `${appearance}:${artId}:${color || ''}`;
  const cached = portraitCache.get(key);
  if (cached) return cached;
  const doc = new DOMParser().parseFromString(raw, 'image/svg+xml');
  const svg = doc.documentElement;
  const viewBox = (svg.getAttribute('viewBox') || '0 0 190 171').split(' ').map(Number);
  const [x, y] = viewBox;
  const character = svg.querySelector('.idle-body');
  let output = raw;
  if (character && viewBox.length === 4) {
    svg.replaceChildren(character);
    svg.setAttribute('viewBox', `${x + 55} ${y + 5} 85 90`);
    output = new XMLSerializer().serializeToString(svg);
  }
  portraitCache.set(key, output);
  return output;
}

/** 墨团模式下把工位插画替换为彩色墨团（保留原 SVG 画布尺寸）。 */
export function inkWorkstationMarkup(
  artId: EmployeeArtId,
  portrait = false,
  color?: string,
): string {
  const doc = new DOMParser().parseFromString(RAW_ART[artId], 'image/svg+xml');
  const svg = doc.documentElement;
  const viewBox = (svg.getAttribute('viewBox') || '10 189 190 171').split(' ').map(Number);
  const [x, y] = viewBox;
  const body = svg.querySelector('.idle-body');
  if (body && viewBox.length === 4) {
    body.innerHTML = `<g transform="translate(${x + 35} ${y - (portrait ? 25 : 17)})">${inkMarkup(artId, false, false, color)}</g>`;
  }
  return new XMLSerializer().serializeToString(svg);
}

const RESULT_PAPER = '<g class="result-paper" transform="translate(__X__ __Y__)">'
  + '<defs><linearGradient id="resultPaperSurface" x1="0" y1="0" x2="0" y2="1">'
  + '<stop stop-color="#FFFFFF"/><stop offset="1" stop-color="#F0F2F3"/></linearGradient></defs>'
  + '<rect x=".5" y="1.5" width="46" height="27" rx="1" fill="#000" opacity=".07"/>'
  + '<path d="M0 26H46V27.2H0Z" fill="#DDE1E3"/>'
  + '<g transform="scale(1 .84)"><rect width="46" height="31" rx="1.2" fill="url(#resultPaperSurface)" stroke="#CFD6DA" stroke-width=".6"/>'
  + '<path d="M7 8H28M7 14H25M7 20H22" stroke="#B8C2C7" stroke-width="1.2" stroke-linecap="round"/>'
  + '<path d="M32 18L35 21L40 14" fill="none" stroke="#34B963" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
  + '</g><path d="M1 .3H45" stroke="white" stroke-width=".5"/></g>';

/** 完成未读：举手挥动完成标记，桌上放结果纸。坐标相对工位 viewBox。 */
export function attachCompletedResult(raw: string, signHref = completedSignUrl): string {
  const doc = new DOMParser().parseFromString(raw, 'image/svg+xml');
  const svg = doc.documentElement;
  const viewBox = (svg.getAttribute('viewBox') || '0 0 190 171').split(' ').map(Number);
  if (viewBox.length < 2) return raw;
  const [x, y] = viewBox;
  const inkHand = svg.querySelector('.ink-arm');
  const originalHand = inkHand
    ? svg.querySelector('.ink-arm:last-child')
    : svg.querySelector('.idle-body > path:nth-child(5)');
  const hand = originalHand ? new XMLSerializer().serializeToString(originalHand) : '';
  originalHand?.setAttribute('visibility', 'hidden');
  const handMarkup = inkHand ? `<g transform="translate(${x + 35} ${y - 17})">${hand}</g>` : hand;
  const href = signHref.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  const sign = `<g class="summon-wave" style="transform-origin:${x + 117}px ${y + 54}px">`
    + `<image href="${href}" x="${x + 115}" y="${y + 25}" width="44" height="44"/>${handMarkup}</g>`;
  const paper = RESULT_PAPER.replace('__X__', String(x + 72)).replace('__Y__', String(y + 88));
  svg.insertAdjacentHTML('beforeend', sign + paper);
  return new XMLSerializer().serializeToString(svg);
}

export function employeeAvatarSrc(artId: EmployeeArtId): string {
  return AVATAR_ART[artId];
}

export function rawArtFor(artId: EmployeeArtId): string {
  return RAW_ART[artId];
}

import packageJson from '../../package.json';

const COMPONENTS = [
  { name: 'ECharts', license: 'Apache-2.0', usage: '图表渲染（懒加载）' },
  { name: 'React', license: 'MIT', usage: '控制台与公开分享页' },
  { name: 'DataV-Vue3', license: 'MIT', usage: '大屏边框/装饰的原始视觉资产' },
  { name: 'DataRoom', license: 'Apache-2.0', usage: 'DataV 资产的二次修改与组件目录格式' },
];

export default function AboutPage() {
  return (
    <div className="page">
      <div className="page-title">
        <div>
          <h1>关于</h1>
          <p className="page-subtitle">版本、第三方组件与许可证</p>
        </div>
      </div>

      <section className="data-card">
        <h2>StaffDeck 数据大屏</h2>
        <p>前端版本：{packageJson.version}</p>
        <p>本仓自身许可证为 AGPL-3.0；完整文本见仓库根目录 LICENSE 与 THIRD_PARTY_NOTICES.md。</p>
      </section>

      <section className="data-card">
        <h2>第三方组件</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>组件</th>
              <th>许可证</th>
              <th>用途</th>
            </tr>
          </thead>
          <tbody>
            {COMPONENTS.map((item) => (
              <tr key={item.name}>
                <td>{item.name}</td>
                <td>{item.license}</td>
                <td>{item.usage}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

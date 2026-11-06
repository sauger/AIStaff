import type { ReactNode } from 'react';

import '@/components/datav/datav.css';
import DvBorderBox1 from '@/components/datav/DvBorderBox1';
import DvDecoration8 from '@/components/datav/DvDecoration8';

/** Tech-wall chrome; loaded lazily so chat bubbles never pay for the SVG assets. */
export default function DashboardFrame({ children }: { children: ReactNode }) {
  return (
    <DvBorderBox1 className="dash-frame" color={['#2cf7fe', '#1d4ed8']} backgroundColor="transparent">
      <div className="dash-frame-inner">{children}</div>
      <div className="dash-frame-deco" aria-hidden="true">
        <DvDecoration8 color={['#2cf7fe', '#3b82f6']} />
      </div>
    </DvBorderBox1>
  );
}

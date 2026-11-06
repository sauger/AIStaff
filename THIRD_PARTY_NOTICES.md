# Third-party notices

This repository includes third-party open-source components. The full texts
shipped with the source are listed below.

## DataRoom / DataV-Vue3 (dashboard visual assets)

- DataRoom: https://github.com/gcpaas/DataRoom — Apache-2.0, commit `1e04b9c`.
- DataV-Vue3: https://github.com/DataV-Team/DataV-Vue3 — MIT.
- Files: `frontend-enterprise/src/components/datav/*.tsx`, derived from
  `dataRoomFront/src/dataRoom/datav/components/{BorderBox1,BorderBox2,BorderBox10,BorderBox13,Decoration5,Decoration8,Decoration11}`.
- License texts: `frontend-enterprise/src/assets/datav/LICENSE` (Apache-2.0)
  and `frontend-enterprise/src/assets/datav/LICENSE-MIT` (MIT).
- Modifications: React rewrite, CSS animations instead of SMIL, local
  `withAlpha` instead of `@jiaminghi/color`.

## ECharts

- https://github.com/apache/echarts — Apache-2.0.
- Used by `frontend-enterprise/src/pages/chat/components/dashboardEchartsRuntime.ts`
  (lazy-loaded chunk) and `dashboardEcharts.ts`.

## React

- https://github.com/facebook/react — MIT.
- Used by the enterprise console and the public dashboard page.

## DB-GPT

- https://github.com/eosphoros-ai/DB-GPT — MIT.
- Only the planner validation / field-error / repair pattern was adapted; no
  source files are vendored.

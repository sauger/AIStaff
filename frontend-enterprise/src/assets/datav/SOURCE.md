# DataV / DataRoom visual assets

The seven React components in `frontend-enterprise/src/components/datav/` are
ports of the following upstream files.

- Repository: https://github.com/gcpaas/DataRoom
- Commit: `1e04b9c41f42c44cc507408c68d44459a21cf51d` (master, 2026-11-06)
- DataRoom license: Apache-2.0 (see `LICENSE`)
- Upstream: DataV-Vue3, https://github.com/DataV-Team/DataV-Vue3, MIT (see `LICENSE-MIT`)
- DataRoom's own `dataRoomFront/src/dataRoom/datav/README.md` states that its
  datav components were copied from DataV-Vue3 and modified.

| React component | Upstream path |
|---|---|
| `DvBorderBox1.tsx` | `dataRoomFront/src/dataRoom/datav/components/BorderBox1/src/BorderBox1.tsx` |
| `DvBorderBox2.tsx` | `dataRoomFront/src/dataRoom/datav/components/BorderBox2/src/BorderBox2.tsx` |
| `DvBorderBox10.tsx` | `dataRoomFront/src/dataRoom/datav/components/BorderBox10/src/BorderBox10.tsx` |
| `DvBorderBox13.tsx` | `dataRoomFront/src/dataRoom/datav/components/BorderBox13/src/BorderBox13.tsx` |
| `DvDecoration5.tsx` | `dataRoomFront/src/dataRoom/datav/components/Decoration5/src/index.vue` |
| `DvDecoration8.tsx` | `dataRoomFront/src/dataRoom/datav/components/Decoration8/src/index.vue` |
| `DvDecoration11.tsx` | `dataRoomFront/src/dataRoom/datav/components/Decoration11/src/index.vue` |

Changes: rewritten as React function components; Vue `autoResize` replaced by
`useAutoResize`; SMIL animations replaced by CSS keyframes; `@jiaminghi/color`
`fade()` replaced by a local `withAlpha`; no external references or scripts.

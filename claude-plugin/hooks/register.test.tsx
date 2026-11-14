import { expect, test } from 'claude-code/testing'

// the header of a 900 x 600 PNG: all the pane reads of it besides the bytes it shows
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAA4QAAAJYCAIAAAAAAAAAAAAAAA=='
const SAVED = '/home/u/.hertzon/shown/20261114-1-Patch_report_S11.png'

const PANE = {
  plugin: 'hertzon',
  component: 'Pane',
  requestId: 'hertzon-results',
  props: {
    title: 'Hertzon',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const BAND = {
  plugin: 'hertzon',
  component: 'AbovePrompt',
  requestId: 'above-prompt',
  props: {
    bodyColumns: 80,
    hasSurvey: false,
    isCollapsed: false,
    isWorking: true,
    maxRows: 3,
    scroll: { offset: 0, bodyRows: 3 },
    view: {},
  },
} as const

test('a picture Hertzon drew appears in the pane, on every surface', async ($, on) => {
  on('command.register', () => ({ value: { command: 'hertzon-results' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('clock.now', () => ({ value: 1000 }))
  on('fs.read', () => ({ value: { base64: PNG } }))
  on('fs.exists', (_, e) => ({ value: String(e.path ?? '').endsWith('.preview.png') }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('tool.call', () => ({
    result: {},
    text: `Patch: report 'S11' (as shown in the Hertzon window). Saved: ${SAVED}`,
  }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'mcp__hertzon__show_result', what: 'report', name: 'S11' })

  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /report 'S11'/ }))?.text).toBe(
      "Patch: report 'S11' (as shown in the Hertzon window)",
    )
    expect(await ui.find({ type: 'Text', text: /1 \/ 1/ })).toBeTruthy()
    await ui.unmount()
  }
})

test('other tools leave the pane empty', async ($, on) => {
  on('tool.call', () => ({ result: {}, text: 'Saved: /tmp/x.png' }))
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text' }))?.text).toBe('No Hertzon results yet.')
  await ui.unmount()
})

test('a running analysis shows its pass above the prompt', async ($, on) => {
  on('clock.now', () => ({ value: 1000 }))
  on('tool.call', () => ({
    result: {},
    text: JSON.stringify({
      setup: 'Setup1',
      state: 'running',
      passes: [{ pass: 7, tets: 14164, delta_s: 0.04012 }],
      converged: false,
    }),
  }))
  await $.tool.call({ tool: 'mcp__hertzon__get_status', setup: 'Setup1' })
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const row = await ui.find({ type: 'Text', text: /Hertzon/ })
  expect(row?.text).toBe('Hertzon: Setup1 running, pass 7, 14,164 tetrahedra, Delta S 0.0401')
  await ui.unmount()
})

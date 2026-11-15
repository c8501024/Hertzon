// Hertzon results beside the chat: every picture Hertzon draws for the assistant (show_result, render_image)
// appears in the "Hertzon" pane, and a running analysis shows its adaptive pass above the prompt.
// Hertzon draws the pictures itself (the same widgets as in its window); this module only shows them.
import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Shown, Solve } from '../types'

const PANE = 'hertzon-results'
const TITLE = 'Hertzon'
const shown = atom({ plugin: 'hertzon', key: 'shown' } as const, [], { shape: 'shown-1' })
const index = atom({ plugin: 'hertzon', key: 'index' } as const, -1)
const solve = atom({ plugin: 'hertzon', key: 'solve' } as const, null, { shape: 'solve-1' })

const KEEP = 50
// a Hertzon tool, whatever name the server was registered under (mcp__hertzon__show_result, ...)
const HERTZON = /^mcp__[^_]*hertzon[^_]*__(show_result|render_image|get_status)$/i
const SVG_LIMIT = 131072

/** The width and height of a PNG from its header (bytes 16 to 23). */
function pngSize(base64: string): { width: number; height: number } {
  const ABC = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  const head: number[] = []
  for (let i = 0; i + 4 <= 32; i += 4) {
    const n = [0, 1, 2, 3].reduce((acc, k) => (acc << 6) | Math.max(0, ABC.indexOf(base64.charAt(i + k))), 0)
    head.push((n >> 16) & 255, (n >> 8) & 255, n & 255)
  }
  const word = (i: number) =>
    (((head[i] ?? 0) << 24) | ((head[i + 1] ?? 0) << 16) | ((head[i + 2] ?? 0) << 8) | (head[i + 3] ?? 0)) >>> 0
  return { width: word(16), height: word(20) }
}

/** The text a tool answered: core joins the text blocks; an MCP result may also come as its blocks. */
function resultText(ran: { text?: string; result?: unknown }): string {
  if (typeof ran.text === 'string') return ran.text
  const r = ran.result as { content?: { type: string; text?: string }[] } | { type: string; text?: string }[]
  const blocks = Array.isArray(r) ? r : r && Array.isArray(r.content) ? r.content : []
  return blocks.filter(b => b.type === 'text' && typeof b.text === 'string').map(b => b.text).join('\n')
}

function solveFrom(text: string, at: number): Solve | null {
  let s: {
    setup?: string; state?: string; converged?: boolean
    passes?: { pass?: number; tets?: number; delta_s?: number | null }[]
  }
  try {
    s = JSON.parse(text)
  } catch {
    return null
  }
  if (!s || typeof s.state !== 'string') return null
  const last = (s.passes && s.passes[s.passes.length - 1]) || {}
  return {
    setup: s.setup ?? '',
    state: s.state,
    pass: last.pass ?? 0,
    tets: last.tets ?? 0,
    deltaS: typeof last.delta_s === 'number' ? last.delta_s : null,
    converged: !!s.converged,
    at,
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'hertzon-results',
      description: 'Show the Hertzon results pane (reports, field plots, model pictures Hertzon drew)',
    })
    return next(e)
  })

  on('command.run', { command: 'hertzon-results' }, async $ => {
    const opened = await $.ui.open({ id: PANE, title: TITLE })
    const n = (await read($, shown)).length
    return {
      text: opened.isPlaced
        ? `Hertzon pane opened (${n} picture${n === 1 ? '' : 's'}).`
        : `The Hertzon pane cannot be shown here (${opened.reason}).`,
    }
  })

  on('tool.call', async ($, e, next) => {
    const m = HERTZON.exec(e.tool)
    const ran = await next(e)
    if (!m || ran.deny !== undefined || ran.isError) return ran
    const text = resultText(ran)
    const now = await $.clock.now()
    if (m[1] === 'get_status') {
      const s = solveFrom(text, now)
      if (s) await update($, solve, () => s)
      return ran
    }
    const saved = /Saved: (.+\.png)\s*$/m.exec(text)
    if (!saved) return ran
    const png = (saved[1] ?? '').trim()
    const label = text.slice(0, saved.index).replace(/[.\s]+$/, '')
    try {
      const { base64 } = await $.fs.read(png, { as: 'bytes' })
      const { width, height } = pngSize(base64)
      const small = png.replace(/\.png$/, '.preview.png')
      const preview = (await $.fs.exists(small)) ? small : png.replace(/\.png$/, '.preview.jpg')
      const item: Shown = { label, png, preview, width, height, at: now }
      await update($, shown, list => [...list, item].slice(-KEEP))
      await update($, index, () => -1)
      await $.ui.open({ id: PANE, title: TITLE })
    } catch {
      // the picture is not on this machine (a remote assistant): nothing to show here
    }
    return ran
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const list = await read($, shown)
    if (list.length === 0) {
      return (
        <Box flexDirection="column">
          <Text dimColor>No Hertzon results yet.</Text>
          <Text dimColor>Ask for one, e.g. "show me S11 and the convergence".</Text>
        </Box>
      )
    }
    const at = await read($, index)
    const i = at < 0 || at >= list.length ? list.length - 1 : at
    const item = list[i]
    if (!item) return <Text dimColor>No Hertzon results yet.</Text>
    const cols = Math.max(20, e.props.bodyColumns ?? e.viewport?.columns ?? 80)
    const go = (j: number) => update($, index, () => (j >= list.length - 1 ? -1 : Math.max(0, j)))
    const nav = (
      <Box>
        <Button key="prev" label="◀" onPress={() => go(i - 1)} />
        <Text> {i + 1} / {list.length} </Text>
        <Button key="next" label="▶" onPress={() => go(i + 1)} />
        {i < list.length - 1 && <Button key="newest" label="Newest" onPress={() => go(list.length - 1)} />}
      </Box>
    )
    let picture
    try {
      if (e.surface === 'terminal') {
        const { Image } = $.ui.resolve(e)
        const { base64 } = await $.fs.read(item.png, { as: 'bytes' })
        // terminal cells are about twice as tall as wide
        const rows = Math.max(4, Math.min(255, Math.round((cols * item.height) / item.width / 2)))
        picture = <Image source={{ png: base64 }} columns={Math.min(255, cols)} rows={rows} alt={item.label} />
      } else {
        const { Svg } = $.ui.resolve(e)
        const { base64 } = await $.fs.read(item.preview, { as: 'bytes' })
        const mime = item.preview.endsWith('.png') ? 'image/png' : 'image/jpeg'
        const svg =
          `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${item.width} ${item.height}">` +
          `<image href="data:${mime};base64,${base64}" width="${item.width}" height="${item.height}"/></svg>`
        picture =
          svg.length <= SVG_LIMIT ? (
            <Svg source={svg} alt={item.label} />
          ) : (
            <Text dimColor>Too large to show here: {item.png}</Text>
          )
      }
    } catch {
      picture = <Text dimColor>The picture is gone: {item.png}</Text>
    }
    return (
      <Box flexDirection="column">
        <Text bold>{item.label}</Text>
        {nav}
        {picture}
        <Text dimColor>{item.png}</Text>
      </Box>
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const s = await read($, solve)
    if (!s || (s.state !== 'running' && s.state !== 'queued')) return next(e)
    const { Text } = $.ui.resolve(e)
    const pass = s.pass ? `, pass ${s.pass}, ${s.tets.toLocaleString('en')} tetrahedra` : ''
    const ds = s.deltaS !== null ? `, Delta S ${s.deltaS.toFixed(4)}` : ''
    return (
      <Text dimColor>
        Hertzon: {s.setup} {s.state}
        {pass}
        {ds}
      </Text>
    )
  })
}

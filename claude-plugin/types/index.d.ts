/** A picture Hertzon drew for the assistant (MCP show_result or render_image), kept in ~/.hertzon/shown. */
export type Shown = {
  /** What it shows, as the tool said it ("Patch: report 'S11' (as shown in the Hertzon window)"). */
  label: string
  /** The PNG as drawn. */
  png: string
  /** A copy small enough for the pane on the desktop surfaces (.preview.png or .preview.jpg). */
  preview: string
  width: number
  height: number
  /** Milliseconds since 1970. */
  at: number
}

/** The last analysis state read with get_status. */
export type Solve = {
  setup: string
  state: string
  pass: number
  tets: number
  deltaS: number | null
  converged: boolean
  at: number
}

declare module 'claude-code' {
  interface PluginState {
    hertzon: {
      shown: Shaped<Shown[]>
      /** Index into shown of the picture on screen; -1: always the newest. */
      index: number
      solve: Shaped<Solve | null>
    }
  }
}

/** Small outline icons for the call controls (24x24, stroke follows the text color). */
type P = { size?: number }

function Svg({ size = 20, children }: P & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  )
}

export const MicIcon = (p: P) => (
  <Svg {...p}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></Svg>
)
export const MicOffIcon = (p: P) => (
  <Svg {...p}><path d="M3 3l18 18M9 9v2a3 3 0 0 0 5.1 2.1M15 10V6a3 3 0 0 0-5.7-1.3M5 11a7 7 0 0 0 11.3 5.5M19 11a7 7 0 0 1-.6 2.8M12 18v3" /></Svg>
)
export const CamIcon = (p: P) => (
  <Svg {...p}><rect x="3" y="6" width="13" height="12" rx="2" /><path d="M16 10l5-3v10l-5-3z" /></Svg>
)
export const CamOffIcon = (p: P) => (
  <Svg {...p}><path d="M3 3l18 18M16 16v0a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h1M10 6h4a2 2 0 0 1 2 2v2l5-3v10" /></Svg>
)
export const CaptionsIcon = (p: P) => (
  <Svg {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M10 10.5a2 2 0 1 0 0 3M17 10.5a2 2 0 1 0 0 3" /></Svg>
)
export const DocIcon = (p: P) => (
  <Svg {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></Svg>
)
export const ChatIcon = (p: P) => (
  <Svg {...p}><path d="M4 5h16v11H9l-5 4z" /></Svg>
)
export const TimerIcon = (p: P) => (
  <Svg {...p}><circle cx="12" cy="13" r="8" /><path d="M12 9v4l2 2M9 2h6" /></Svg>
)
export const HangUpIcon = (p: P) => (
  <Svg {...p}><path d="M3 15.5c4.9-4.7 13.1-4.7 18 0l-2.3 2.3-3.2-1.6v-2.4a10 10 0 0 0-7 0v2.4l-3.2 1.6z" /></Svg>
)

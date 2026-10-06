import * as React from "react"

const MOBILE_BREAKPOINT = 768

// Rewritten from shadcn's generated setState-in-effect version to
// useSyncExternalStore — same fix as lib/run-metadata-store.ts's
// useRunMetadata, for the same reason: reading an external, change-emitting
// source (a MediaQueryList) is exactly what this hook is for, and this
// avoids the "setState inside an effect" lint flag an effect+setState pair
// would otherwise trip, plus gives a well-defined, SSR-safe server snapshot.
function subscribe(onChange: () => void) {
  const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
  mql.addEventListener("change", onChange)
  return () => mql.removeEventListener("change", onChange)
}

function getSnapshot() {
  return window.innerWidth < MOBILE_BREAKPOINT
}

function getServerSnapshot() {
  return false
}

export function useIsMobile() {
  return React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

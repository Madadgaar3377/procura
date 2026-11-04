export default function Loading() {
  return <main className="route-loading" role="status" aria-live="polite" aria-label="Loading page">
    <div className="route-loading-mark">P</div>
    <div className="route-loading-copy"><strong>Loading workspace</strong><span>Preparing your page…</span></div>
    <div className="route-loading-track"><i/></div>
  </main>;
}

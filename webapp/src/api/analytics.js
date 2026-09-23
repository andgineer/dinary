export async function fetchAnalyticsSummary() {
  const res = await fetch("/api/analytics/summary");
  if (!res.ok) throw new Error(`analytics summary ${res.status}`);
  return res.json();
}

export async function fetchEventDetail(eventId) {
  const res = await fetch(`/api/analytics/events/${encodeURIComponent(eventId)}`);
  if (!res.ok) throw new Error(`analytics event ${res.status}`);
  return res.json();
}

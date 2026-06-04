export async function fetchAnalyticsSummary() {
  const res = await fetch("/api/analytics/summary");
  if (!res.ok) throw new Error(`analytics summary ${res.status}`);
  return res.json();
}

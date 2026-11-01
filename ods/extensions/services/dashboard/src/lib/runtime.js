// A Windows-native install (dashboard-api ODS_RUNTIME=native) runs without
// Docker. /api/status lists the Docker-only features it lacks in
// runtime.unavailable; the UI does not offer those.
export function runtimeUnavailable(status, feature) {
  const unavailable = status?.runtime?.unavailable
  return Array.isArray(unavailable) && unavailable.includes(feature)
}

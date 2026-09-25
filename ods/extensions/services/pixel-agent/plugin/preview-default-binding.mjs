// Only the guard supplies publication evidence. Model input never selects a
// fallback by URL, directory, or a partially copied digest.
export function bindDefaultPreviewInspection(params, publication) {
  if (!params || typeof params !== 'object' || Array.isArray(params) ||
      Object.keys(params).sort().join(',') !== 'steps,viewport' ||
      !publication || typeof publication.sha256 !== 'string' ||
      !/^[a-f0-9]{64}$/.test(publication.sha256) ||
      publication.siteId !== `site-${publication.sha256.slice(0, 24)}`) return undefined;
  return {...params, siteId:publication.siteId, sha256:publication.sha256};
}

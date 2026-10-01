import { processZipArchive, ZIP_LIMITS } from './pixelZipArchiveCore'

globalThis.onmessage = async ({ data }) => {
  try {
    const { file, selectedPaths } = data
    if (!file || !file.size || file.size > ZIP_LIMITS.compressedBytes) throw new Error('Choose a nonempty ZIP no larger than 4 MB.')
    const result = await processZipArchive(new Uint8Array(await file.arrayBuffer()), file.name, selectedPaths)
    globalThis.postMessage({ result })
  } catch (error) {
    globalThis.postMessage({ error: error instanceof Error ? error.message : 'ZIP could not be read.' })
  }
}

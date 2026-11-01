import fs from 'node:fs'
import path from 'node:path'

/** Files produced under the run outdir, newest first. */
export interface ArtifactInfo {
  name: string
  bytes: number
  mtimeMs: number
  fresh: boolean
}

export interface ArtifactCollection {
  artifacts: ArtifactInfo[]
  noiseFiltered: number
}

export function collectArtifacts(
  outdirWsl: string | null,
  t0: number,
  includeNoise: unknown,
): ArtifactCollection {
  // ---- 产物清单：默认过滤噪音
  const isNoise = (n: string): boolean =>
    n.indexOf('__pycache__') === 0 ||
    /\.pyc$/.test(n) ||
    /\.blend[12]$/.test(n) ||
    n.indexOf('tmp') === 0 ||
    n === '.DS_Store'
  let artifacts: ArtifactInfo[] = []
  let noiseFiltered = 0
  if (outdirWsl) {
    try {
      const all: Array<ArtifactInfo & { noise: boolean }> = fs
        .readdirSync(outdirWsl)
        .map((n) => {
          const p = path.join(outdirWsl, n)
          let st: fs.Stats | null
          try {
            st = fs.statSync(p)
          } catch (e) {
            st = null
          }
          return st && st.isFile()
            ? {
                name: n,
                bytes: st.size,
                mtimeMs: st.mtimeMs,
                fresh: st.mtimeMs >= t0 - 2000,
                noise: isNoise(n),
              }
            : null
        })
        .filter(
          (
            x,
          ): x is {
            name: string
            bytes: number
            mtimeMs: number
            fresh: boolean
            noise: boolean
          } => x !== null,
        )
      noiseFiltered = all.filter((a) => a.noise).length
      artifacts = (includeNoise ? all : all.filter((a) => !a.noise))
        .sort((a, b) => b.mtimeMs - a.mtimeMs)
        .slice(0, 40)
        .map((a) => ({ name: a.name, bytes: a.bytes, mtimeMs: a.mtimeMs, fresh: a.fresh }))
    } catch (e) {
      artifacts = []
    }
  }
  return { artifacts, noiseFiltered }
}
